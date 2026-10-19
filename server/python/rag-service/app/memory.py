"""대화 맥락 관리.

최근 4턴은 원문으로 두고 그 이전은 하나의 요약본으로 관리한다. 대화가 길어져도
프롬프트 토큰 예산 안에 맥락이 들어가게 하기 위해서다. 요약은 Redis에 캐시해
매번 다시 만들지 않는다.
"""
import structlog
import redis.asyncio as redis

from app.db import Db
from app.generation.base import AnswerGenerator
from app.prompt_files import prompt

log = structlog.get_logger()

# 본문은 prompts/rewrite_question.md
REWRITE_SYSTEM = prompt("rewrite_question")

# 본문은 prompts/conversation_summary.md
SUMMARY_SYSTEM = prompt("conversation_summary")


class ConversationMemory:
    def __init__(
        self,
        redis_client: redis.Redis,
        generator: AnswerGenerator,
        db: Db,
        recent_turns: int = 4,
        summary_after_turns: int = 5,
        summary_ttl_seconds: int = 7 * 24 * 3600,
    ):
        self._redis = redis_client
        self._generator = generator
        self._db = db
        # 맥락으로 쓰는 최근 턴 수. 답변 프롬프트의 [최근 대화]도 같은 수를 쓴다(GraphNodes.generate)
        self.recent_turns = recent_turns
        # 대화가 이 턴 수를 넘으면 최근 턴 이전을 요약해 캐시한다
        self._summary_after_turns = summary_after_turns
        self._summary_ttl_seconds = summary_ttl_seconds

    async def summary_of(self, conversation_id: int) -> str | None:
        return await self._redis.get(self._summary_key(conversation_id))

    async def context_of(self, conversation_id: int) -> str | None:
        """질의 재작성·계획에 줄 대화 맥락([이전 요약]·[최근 대화]). 첫 질문이면 None."""
        recent = await self._db.recent_turns(conversation_id, self.recent_turns)
        if not recent:
            return None

        summary = await self.summary_of(conversation_id)
        parts = []
        if summary:
            parts.append(f"[이전 요약]\n{summary}\n")
        turns_text = "\n".join(f"{role}: {content}" for role, content, _ in recent)
        parts.append(f"[최근 대화]\n{turns_text}")
        return "\n".join(parts)

    async def rewrite_question(self, conversation_id: int, question: str) -> str:
        """문맥 의존 질문을 독립형 질문으로 바꾼다. 실패하면 원문을 그대로 쓴다.

        질의 계획(QUERY_PLANNER_ENABLED)을 끈 경우에만 쓴다. 켜져 있으면 계획 호출이 재작성까지 한다.
        """
        context = await self.context_of(conversation_id)
        if context is None:
            return question
        prompt = f"{context}\n\n[현재 질문]\n{question}\n\n검색 질의:"

        rewritten = await self._generator.complete(REWRITE_SYSTEM, prompt, 200)
        # 재작성이 원문 그대로 돌아오거나 지나치게 길면 원문을 쓴다
        if not rewritten or not rewritten.strip() or len(rewritten) > len(question) * 6:
            return question
        return rewritten.strip()

    async def refresh_summary(self, conversation_id: int) -> None:
        """대화가 길어지면 이전 내용을 요약해 캐시한다."""
        if await self._db.turn_count(conversation_id) <= self._summary_after_turns:
            return
        try:
            all_turns = await self._db.recent_turns(conversation_id, 40)
            older = all_turns[: -self.recent_turns] if len(all_turns) > self.recent_turns else []
            if not older:
                return
            body = "\n".join(f"{role}: {content}" for role, content, _ in older)

            summary = await self._generator.complete(SUMMARY_SYSTEM, body, 400)
            if summary and summary.strip():
                await self._redis.set(self._summary_key(conversation_id), summary.strip(), ex=self._summary_ttl_seconds)
        except Exception as e:  # noqa: BLE001 — 요약 실패가 답변을 막지 않는다
            log.warning("대화 요약 실패", conversation_id=conversation_id, error=str(e))

    def recent_turn_texts(self, turns: list[tuple[str, str, object]]) -> list[str]:
        return [f"{role}: {content}" for role, content, _ in turns]

    def _summary_key(self, conversation_id: int) -> str:
        return f"conv:summary:{conversation_id}"

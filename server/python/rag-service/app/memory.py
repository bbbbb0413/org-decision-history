"""대화 맥락 관리.

최근 4턴은 원문으로 두고 그 이전은 하나의 요약본으로 관리한다. 대화가 길어져도
프롬프트 토큰 예산 안에 맥락이 들어가게 하기 위해서다. 요약은 Redis에 캐시해
매번 다시 만들지 않는다.
"""
import structlog
import redis.asyncio as redis

from app.db import Db
from app.generation.base import AnswerGenerator

log = structlog.get_logger()

SUMMARY_THRESHOLD = 5
RECENT_TURNS = 4
SUMMARY_TTL_SECONDS = 7 * 24 * 3600

REWRITE_SYSTEM = """너는 검색 질의를 다듬는 도구다. 이전 대화 맥락을 반영해, 그 자체로 뜻이 통하는
한 문장의 검색 질의를 만든다. 설명이나 인사말을 붙이지 않고 질의만 출력한다.
대명사와 생략된 주어를 앞 대화에서 찾아 채운다.
"""

SUMMARY_SYSTEM = """너는 대화를 요약하는 도구다. 이후 질문의 맥락으로 쓸 수 있도록
결정된 사항과 언급된 문서, 미해결 질문을 3문장 안으로 정리한다.
"""


class ConversationMemory:
    def __init__(self, redis_client: redis.Redis, generator: AnswerGenerator, db: Db):
        self._redis = redis_client
        self._generator = generator
        self._db = db

    async def summary_of(self, conversation_id: int) -> str | None:
        return await self._redis.get(self._summary_key(conversation_id))

    async def rewrite_question(self, conversation_id: int, question: str) -> str:
        """문맥 의존 질문을 독립형 질문으로 바꾼다. 실패하면 원문을 그대로 쓴다."""
        recent = await self._db.recent_turns(conversation_id, RECENT_TURNS)
        if not recent:
            return question

        summary = await self.summary_of(conversation_id)
        parts = []
        if summary:
            parts.append(f"[이전 요약]\n{summary}\n")
        turns_text = "\n".join(f"{role}: {content}" for role, content, _ in recent)
        parts.append(f"[최근 대화]\n{turns_text}")
        parts.append(f"\n[현재 질문]\n{question}\n\n검색 질의:")
        prompt = "\n".join(parts)

        rewritten = await self._generator.complete(REWRITE_SYSTEM, prompt, 200)
        # 재작성이 원문 그대로 돌아오거나 지나치게 길면 원문을 쓴다
        if not rewritten or not rewritten.strip() or len(rewritten) > len(question) * 6:
            return question
        return rewritten.strip()

    async def refresh_summary(self, conversation_id: int) -> None:
        """대화가 길어지면 이전 내용을 요약해 캐시한다."""
        if await self._db.turn_count(conversation_id) <= SUMMARY_THRESHOLD:
            return
        try:
            all_turns = await self._db.recent_turns(conversation_id, 40)
            older = all_turns[: -RECENT_TURNS] if len(all_turns) > RECENT_TURNS else []
            if not older:
                return
            body = "\n".join(f"{role}: {content}" for role, content, _ in older)

            summary = await self._generator.complete(SUMMARY_SYSTEM, body, 400)
            if summary and summary.strip():
                await self._redis.set(self._summary_key(conversation_id), summary.strip(), ex=SUMMARY_TTL_SECONDS)
        except Exception as e:  # noqa: BLE001 — 요약 실패가 답변을 막지 않는다
            log.warning("대화 요약 실패", conversation_id=conversation_id, error=str(e))

    def recent_turn_texts(self, turns: list[tuple[str, str, object]]) -> list[str]:
        return [f"{role}: {content}" for role, content, _ in turns]

    def _summary_key(self, conversation_id: int) -> str:
        return f"conv:summary:{conversation_id}"

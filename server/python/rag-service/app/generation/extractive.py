"""API 키가 없을 때 쓰는 대체 답변 생성기.

근거 청크에서 질문과 겹치는 문장을 추려 그대로 인용한다. 문장을 새로 만들지
않는다. 요약도 종합도 하지 않으므로 읽기에 거칠지만, 근거에 없는 말을
지어내지 않는다는 이 제품의 핵심 규칙만큼은 지킨다.
"""
import re

import structlog

from app.generation.models import AnswerChunk, AnswerRequest, AnswerStatus
from app.generation.base import OnChunk

log = structlog.get_logger()

MAX_SENTENCES_PER_SOURCE = 2
_MODEL_NAME = "extractive-fallback"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")


class ExtractiveAnswerGenerator:
    def __init__(self):
        log.warning(
            "GROQ_API_KEY가 비어 있어 근거 발췌 방식으로 답한다. "
            "문장을 새로 만들지 않으므로 요약된 답변을 기대할 수 없다."
        )

    async def generate(self, request: AnswerRequest, on_chunk: OnChunk) -> None:
        evidence = request.evidence
        if not evidence:
            await on_chunk(AnswerChunk.finished(AnswerStatus.NO_EVIDENCE, _MODEL_NAME))
            return

        await on_chunk(AnswerChunk.delta("근거 문서에서 질문과 관련된 대목을 옮긴다.\n\n"))

        for i, hit in enumerate(evidence):
            extracted = self._extract(hit.content or "", request.question)
            if extracted.strip():
                await on_chunk(AnswerChunk.delta(f"[{i + 1}] {extracted}\n\n"))

        await on_chunk(AnswerChunk.delta("위 내용은 원문을 그대로 옮긴 것이다. 요약과 종합은 하지 않았다.\n"))
        # 발췌는 종합을 거치지 않아 근거가 얇을 수 있다. 상태로 그 사실을 남긴다.
        await on_chunk(AnswerChunk.finished(AnswerStatus.LOW_CONFIDENCE, _MODEL_NAME))

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        # 재작성과 요약은 LLM 없이는 의미가 없다. 원문을 그대로 돌려준다.
        return user_prompt

    def _extract(self, content: str, question: str) -> str:
        """질문의 낱말이 가장 많이 겹치는 문장을 고른다."""
        terms = [t.lower() for t in question.split() if len(t) >= 2]

        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(content)]
        candidates = [s for s in sentences if len(s) >= 10 and not s.startswith("[문서]")]
        candidates.sort(key=lambda s: self._overlap(s, terms), reverse=True)
        return " ".join(candidates[:MAX_SENTENCES_PER_SOURCE])

    def _overlap(self, sentence: str, terms: list[str]) -> int:
        lower = sentence.lower()
        return sum(1 for t in terms if t in lower)

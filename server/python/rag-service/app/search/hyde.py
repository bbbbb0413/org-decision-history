from __future__ import annotations
from typing import TYPE_CHECKING
import structlog
from app.generation.models import AnswerChunk, AnswerRequest

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator

log = structlog.get_logger()


class HydeService:
    """단문/추상적 질문에 대해 가상 문서를 생성하여 벡터 검색 적중률을 높인다."""

    def __init__(self, generator: AnswerGenerator):
        self._generator = generator

    async def generate_hypothetical_document(self, question: str) -> str | None:
        prompt = (
            "다음 질문에 대해 사내 문서에 있을 법한 간결하고 사실적인 가상 답변을 2-3문장으로 작성하세요.\n"
            "이 가상 답변은 문서 검색 임베딩 쿼리로 사용됩니다. 오직 답변 내용만 출력하세요.\n\n"
            f"질문: {question}"
        )

        request = AnswerRequest(
            question=prompt,
            summary=None,
            recent_turns=[],
            evidence=[],
        )

        buffer: list[str] = []

        async def on_chunk(chunk: AnswerChunk) -> None:
            if chunk.text:
                buffer.append(chunk.text)

        try:
            await self._generator.generate(request, on_chunk)
            hypo_doc = "".join(buffer).strip()
            if hypo_doc:
                log.info("HyDE 가상 문서 생성 완료", question=question, hypo_doc_len=len(hypo_doc))
                return hypo_doc
        except Exception as e:
            log.warning("HyDE 가상 문서 생성 실패", error=str(e))

        return None


__all__ = ["HydeService"]

from __future__ import annotations
import re
from typing import TYPE_CHECKING
import structlog
from app.generation.models import AnswerChunk, AnswerRequest

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator

log = structlog.get_logger()

MAX_SUB_QUERIES = 3
_DASH_PATTERN = re.compile(r"^[-*\d.]+\s*")


class QueryDecomposer:
    """복합 질문을 독립적인 하위 검색 쿼리들로 분해한다."""

    def __init__(self, generator: AnswerGenerator):
        self._generator = generator

    async def decompose(self, question: str) -> list[str]:
        prompt = (
            f"다음 복합 질문을 최대 {MAX_SUB_QUERIES}개의 검색용 단순 질문으로 분해하세요.\n"
            "각 질문을 새 줄에 '- '로 시작하여 질문만 나열하세요.\n\n"
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
            raw = "".join(buffer).strip()
            lines = [line.strip() for line in raw.split("\n") if line.strip()]
            sub_queries = [_DASH_PATTERN.sub("", line).strip() for line in lines]
            sub_queries = [q for q in sub_queries if q][:MAX_SUB_QUERIES]

            if sub_queries:
                log.info("복합 질문 분해 완료", question=question, sub_queries=sub_queries)
                return sub_queries
        except Exception as e:
            log.warning("질문 분해 실패 (원본 질문 유지)", error=str(e))

        return [question]


__all__ = ["QueryDecomposer"]

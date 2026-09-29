from __future__ import annotations
from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING
import structlog
from app.generation.models import AnswerChunk, AnswerRequest

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator
    from app.search.models import SearchHit

log = structlog.get_logger()


@dataclass(frozen=True)
class CritiqueResult:
    answered: bool
    confidence: float
    missing: list[str]
    next_query: str | None

    @classmethod
    def fallback(cls) -> "CritiqueResult":
        return cls(answered=True, confidence=1.0, missing=[], next_query=None)


class CritiqueGenerator:
    """답변과 검색 근거를 대조하여 환각 및 근거 일치성을 자체 평가한다."""

    def __init__(self, generator: AnswerGenerator):
        self._generator = generator

    async def evaluate(
        self, question: str, answer: str, evidence: list[SearchHit]
    ) -> CritiqueResult:
        if not evidence or not answer.strip():
            return CritiqueResult.fallback()

        context_summary = "\n".join(
            f"[{i + 1}] {(h.content or '')[:250]}" for i, h in enumerate(evidence[:3])
        )

        prompt = (
            "당신은 RAG 시스템의 답변 품질과 환각을 검증하는 평가관입니다.\n"
            "아래 질문, 검색된 컨텍스트(근거), 생성된 답변을 대조하여 오직 유효한 JSON 형식으로만 평가하세요.\n"
            "```json\n"
            "{\n"
            '  "answered": true/false,\n'
            '  "confidence": 0.0 ~ 1.0,\n'
            '  "missing": ["누락된 정보1", ...],\n'
            '  "nextQuery": "근거가 부족할 경우 보충 검색할 쿼리"\n'
            "}\n"
            "```\n\n"
            f"질문: {question}\n\n"
            f"컨텍스트:\n{context_summary}\n\n"
            f"답변:\n{answer}"
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
            return self._parse_critique(raw)
        except Exception as e:
            log.warning("Critique 평가 실패 (fallback 통과)", error=str(e))
            return CritiqueResult.fallback()

    def _parse_critique(self, raw: str) -> CritiqueResult:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return CritiqueResult.fallback()

        try:
            parsed = json.loads(match.group(0))
            answered = parsed.get("answered") is True
            confidence = float(parsed.get("confidence", 1.0))
            missing_raw = parsed.get("missing")
            missing = [str(m) for m in missing_raw] if isinstance(missing_raw, list) else []
            next_q = parsed.get("nextQuery")
            next_query = str(next_q).strip() if next_q else None
            return CritiqueResult(
                answered=answered,
                confidence=confidence,
                missing=missing,
                next_query=next_query,
            )
        except Exception as e:
            log.warning("Critique JSON 파싱 실패 (fallback 통과)", error=str(e), raw=raw[:100])
            return CritiqueResult.fallback()


__all__ = ["CritiqueResult", "CritiqueGenerator"]

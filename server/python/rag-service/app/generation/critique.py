from __future__ import annotations
from dataclasses import dataclass
import json
import re
from typing import TYPE_CHECKING
import structlog
from app.prompt_files import prompt

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator
    from app.search.models import SearchHit

log = structlog.get_logger()

# 본문은 prompts/critique.md
CRITIQUE_SYSTEM = prompt("critique")
MAX_CRITIQUE_TOKENS = 300
# 근거 청크 하나당 넣는 최대 글자 수. 청크는 대개 700토큰(한글 약 1,000자) 이하라 거의 잘리지 않는다
MAX_EVIDENCE_CHARS = 1000


@dataclass(frozen=True)
class CritiqueResult:
    answered: bool
    confidence: float
    missing: list[str]
    next_query: str | None
    # False 면 모델이 평가하지 않았다(키 없음·호출 실패·파싱 실패). 통과로 취급하되 지표에는 넣지 않는다
    evaluated: bool = True

    @classmethod
    def fallback(cls) -> "CritiqueResult":
        return cls(answered=True, confidence=1.0, missing=[], next_query=None, evaluated=False)


class CritiqueGenerator:
    """답변과 검색 근거를 대조하여 환각 및 근거 일치성을 자체 평가한다.

    답변 생성(`generate`)이 아니라 경량 호출(`complete`)을 쓴다. `generate` 는 근거가 비어 있으면
    모델을 부르지 않고 NO_EVIDENCE 로 끝나므로 평가 결과가 나오지 않는다.
    """

    def __init__(self, generator: AnswerGenerator):
        self._generator = generator

    async def evaluate(
        self, question: str, answer: str, evidence: list[SearchHit]
    ) -> CritiqueResult:
        if not evidence or not answer.strip():
            return CritiqueResult.fallback()

        # 답변을 만들 때 본 근거 전부를 같은 번호로 넘긴다. 일부만 보여주면 답변이 인용한 근거를
        # 평가자가 못 봐서, 뒷받침된 답변도 "근거 없음"으로 깎인다
        context_summary = "\n".join(
            f"[{i + 1}] {(h.content or '')[:MAX_EVIDENCE_CHARS]}" for i, h in enumerate(evidence)
        )

        prompt = (
            f"질문: {question}\n\n"
            f"컨텍스트:\n{context_summary}\n\n"
            f"답변:\n{answer}"
        )

        try:
            raw = (await self._generator.complete(CRITIQUE_SYSTEM, prompt, MAX_CRITIQUE_TOKENS)).strip()
        except Exception as e:  # noqa: BLE001 — 평가 실패가 답변을 막지 않는다
            log.warning("Critique 평가 실패 (fallback 통과)", error=str(e))
            return CritiqueResult.fallback()

        # complete 는 LLM 이 없거나 실패하면 입력을 그대로 돌려준다
        if raw == prompt.strip():
            return CritiqueResult.fallback()
        return self._parse_critique(raw)

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

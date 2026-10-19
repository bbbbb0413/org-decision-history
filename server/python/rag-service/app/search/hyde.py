from __future__ import annotations
from typing import TYPE_CHECKING
import structlog
from app.prompt_files import prompt

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator

log = structlog.get_logger()

# 본문은 prompts/hyde.md
HYDE_SYSTEM = prompt("hyde")
MAX_HYDE_TOKENS = 200


class HydeService:
    """단문/추상적 질문에 대해 가상 문서를 생성하여 벡터 검색 적중률을 높인다.

    답변 생성(`generate`)이 아니라 경량 호출(`complete`)을 쓴다. `generate` 는 근거가 비어 있으면
    모델을 부르지 않고 NO_EVIDENCE 로 끝나므로 가상 문서가 나오지 않는다.
    """

    def __init__(self, generator: AnswerGenerator):
        self._generator = generator

    async def generate_hypothetical_document(self, question: str) -> str | None:
        try:
            hypo_doc = (await self._generator.complete(HYDE_SYSTEM, question, MAX_HYDE_TOKENS)).strip()
        except Exception as e:  # noqa: BLE001 — HyDE 실패가 검색을 막지 않는다
            log.warning("HyDE 가상 문서 생성 실패", error=str(e))
            return None

        # complete 는 LLM 이 없거나 실패하면 입력을 그대로 돌려준다. 원래 질문과 같으면 만든 것이 없다
        if not hypo_doc or hypo_doc == question.strip():
            return None
        log.info("HyDE 가상 문서 생성 완료", question=question, hypo_doc_len=len(hypo_doc))
        return hypo_doc


__all__ = ["HydeService"]

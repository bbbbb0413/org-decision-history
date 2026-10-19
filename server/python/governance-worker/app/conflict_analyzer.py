"""LLM 기반 핵심 조항 추출 및 Zero-shot NLI 모순(Conflict) 분석기."""
import json
import re
import structlog
from history_shared.llm import LlmUnavailableError

from app.generation import AnswerGenerator
from app.models import ConflictDetectionResult
from app.prompt_files import prompt

log = structlog.get_logger()

# 본문은 prompts/assertion_extraction.md
ASSERTION_EXTRACTION_SYSTEM = prompt("assertion_extraction")

ASSERTION_EXTRACTION_USER = """[문서 본문]:
{document_text}

JSON 배열:"""

# 본문은 prompts/conflict_nli.md
CONFLICT_NLI_SYSTEM = prompt("conflict_nli")

CONFLICT_NLI_USER = """[신규 조항]: {new_clause} (문서: {new_doc_title})
[기존 정책 조항]: {existing_clause} (문서: {existing_doc_title})

JSON 응답:"""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


def _parse_json(text: str) -> dict | list:
    cleaned = text.strip()
    match = _JSON_BLOCK.search(cleaned)
    if match:
        cleaned = match.group(1).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        start_idx = -1
        end_idx = -1
        for i, ch in enumerate(cleaned):
            if ch in ('[', '{'):
                start_idx = i
                break
        for i in range(len(cleaned) - 1, -1, -1):
            if cleaned[i] in (']', '}'):
                end_idx = i + 1
                break
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            return json.loads(cleaned[start_idx:end_idx])
        raise


class ConflictAnalyzer:
    def __init__(self, generator: AnswerGenerator):
        self._generator = generator

    async def _invoke_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 1000) -> str:
        return await self._generator.complete(system_prompt, user_prompt, max_tokens)

    async def extract_assertions(self, document_text: str, max_chars: int = 4000) -> list[str]:
        if not document_text or not document_text.strip():
            return []

        clipped = document_text[:max_chars]
        prompt = ASSERTION_EXTRACTION_USER.format(document_text=clipped)

        try:
            resp_text = await self._invoke_llm(ASSERTION_EXTRACTION_SYSTEM, prompt, max_tokens=1000)
            data = _parse_json(resp_text)
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
            if isinstance(data, dict) and "assertions" in data:
                return [str(item).strip() for item in data["assertions"] if str(item).strip()]
        except LlmUnavailableError:
            # 한도 초과 등은 "조항 없음"이 아니다. 재시도(XAUTOCLAIM, 5회 뒤 DLQ)에 맡긴다
            raise
        except Exception as e:
            log.warning("문서 핵심 조항 추출 응답을 해석하지 못함 (이 창은 건너뜀)", error=str(e))
            return []

        # 응답 형식이 어긋나면 키워드로 규범 문장을 골라 대신 판정하지 않는다 — 상충 판정도 같은 LLM 이 해야 하고,
        # "한다" 같은 낱말은 거의 모든 서술문에 걸려 사실상 "앞의 몇 문장"을 엉뚱하게 판정하게 된다
        log.warning("문서 핵심 조항 추출 응답이 배열이 아님 (이 창은 건너뜀)", response_type=type(data).__name__)
        return []

    async def analyze_contradiction(
        self,
        new_doc_id: int,
        new_doc_title: str,
        new_clause: str,
        existing_doc_id: int,
        existing_doc_title: str,
        existing_clause: str,
    ) -> ConflictDetectionResult | None:
        prompt = CONFLICT_NLI_USER.format(
            new_doc_title=new_doc_title,
            new_clause=new_clause,
            existing_doc_title=existing_doc_title,
            existing_clause=existing_clause,
        )

        try:
            resp_text = await self._invoke_llm(CONFLICT_NLI_SYSTEM, prompt, max_tokens=1000)
            data = _parse_json(resp_text)
            if not isinstance(data, dict):
                return None

            # has_conflict 가 판정이고 conflict_score 는 그 심각도다(프롬프트가 "상충이 없으면 false, 0.0"으로 정한다).
            # 판정이 "상충 아님"이면 점수와 상관없이 버린다 — "아님"인데 점수가 높은 응답은 모델이 스스로 어긋난
            # 것이라 상충으로 기록·알림할 근거가 못 된다. bool() 로 받으면 문자열 "false" 도 참이 되므로 True 만 인정한다
            if data.get("has_conflict") is not True:
                return None
            conflict_score = min(max(float(data.get("conflict_score", 0.0)), 0.0), 1.0)

            conflict_type = str(data.get("conflict_type", "DIRECT_CONTRADICTION"))
            reasoning = str(data.get("reasoning", "정책 내용 상충"))
            affected_teams = [str(t) for t in data.get("affected_teams", [])]
            recommended_action = str(data.get("recommended_action", "재검토 필요"))

            return ConflictDetectionResult(
                source_doc_id=new_doc_id,
                target_doc_id=existing_doc_id,
                conflict_type=conflict_type,
                severity_score=conflict_score,
                source_assertion=new_clause,
                target_assertion=existing_clause,
                reason=reasoning,
                impacted_teams=affected_teams,
                suggested_action=recommended_action,
            )
        except LlmUnavailableError:
            # 한도 초과 등은 "상충 아님"이 아니다. 삼키면 상충을 조용히 놓치고, 검사가 끝난 것으로 보고
            # 개정 전 판정 정리(SUPERSEDED)까지 돈다. 재시도에 맡긴다
            raise
        except Exception as e:
            log.warning("NLI 상충 분석 실패", error=str(e))
            return None

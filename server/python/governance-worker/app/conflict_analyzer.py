"""LLM 기반 핵심 조항 추출 및 Zero-shot NLI 모순(Conflict) 분석기."""
import json
import re
import structlog

from app.generation import AnswerGenerator
from app.models import ConflictDetectionResult

log = structlog.get_logger()

ASSERTION_EXTRACTION_SYSTEM = """너는 엔터프라이즈 사내 거버넌스 및 정책 감사 전문가다.
문서 본문에서 다른 사내 규정이나 의사결정과 상충할 가능성이 있는 핵심 규범/정책/의사결정 조항을 최대 3~5개만 추출하라.
단순 서술이나 소개글은 제외하고, 권한, 의무, 허용, 금지, 기준, 프로세스 관련 문장만 추출하라.
반드시 JSON 문자열 배열 형태(예: ["조항1", "조항2"])로만 응답하라. 마크다운이나 기타 해설을 포함하지 마라."""

ASSERTION_EXTRACTION_USER = """[문서 본문]:
{document_text}

JSON 배열:"""

CONFLICT_NLI_SYSTEM = """당신은 엔터프라이즈 사내 거버넌스 및 정책 감사 전문가입니다.
새로 제안된 [신규 조항]이 과거 승인된 [기존 정책 조항]과 상충하거나 모순되는지 분석하십시오.
반드시 아래 JSON 스키마 형식으로만 응답하십시오:
{
  "has_conflict": true,
  "conflict_score": 0.85,
  "conflict_type": "DIRECT_CONTRADICTION",
  "reasoning": "신규 조항은 HTTP 통신을 허용하나 기존 규정은 mTLS를 필수로 요구함",
  "affected_teams": ["인프라보안팀", "결제개발팀"],
  "recommended_action": "보안팀 사전 승인 필요"
}

상충이나 모순이 없다면 has_conflict: false, conflict_score: 0.0, conflict_type: "NONE", reasoning: "상충 없음", affected_teams: [], recommended_action: "없음" 으로 응답하십시오."""

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
        except Exception as e:
            log.warning("문서 핵심 조항 추출 실패 (폴백 적용)", error=str(e))

        # 폴백: 문장 단위로 분할하여 규범/정책 관련 문장 추출
        sentences = re.split(r"(?<=[.!?\n])\s+", clipped)
        keywords = ["한다", "하여야", "금지", "허용", "제한", "의무", "원칙", "필수", "축소", "단축", "종속", "폐지"]
        fallback_assertions = []
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) >= 15 and any(k in s_clean for k in keywords):
                fallback_assertions.append(s_clean)
                if len(fallback_assertions) >= 3:
                    break

        if not fallback_assertions and sentences:
            fallback_assertions = [sentences[0].strip()]

        return fallback_assertions

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

            has_conflict = bool(data.get("has_conflict", False))
            conflict_score = float(data.get("conflict_score", 0.0))

            if not has_conflict and conflict_score < 0.5:
                return None

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
        except Exception as e:
            log.warning("NLI 상충 분석 실패", error=str(e))
            return None

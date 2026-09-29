"""LLM 기반 의사결정 지식 그래프 엔티티 및 인과 관계 추출기."""
import json
import re
from typing import Any, Dict, Protocol
import structlog
from .schema import NODE_LABELS, REL_TYPES

log = structlog.get_logger()


class CompletionGenerator(Protocol):
    """이 모듈이 실제로 쓰는 부분만 담은 최소 인터페이스.

    rag-service·governance-worker는 각자 다른 LLM 클라이언트 클래스를 쓰지만(모델이
    다르다), 둘 다 이 메서드 하나만 구조적으로 맞으면 된다 — 그래서 이 공용 모듈이
    어느 한쪽 서비스의 `app.generation`을 끌어오지 않아도 된다.
    """

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
        ...


GRAPH_EXTRACTION_SYSTEM = """너는 엔터프라이즈 사내 지식 그래프(Knowledge Graph) 및 인과 추론 전문가다.
주어진 의사결정 문서 본문에서 주요 엔티티(시스템, 팀/부서, 규정)와 이들 간의 인과/대체/종속 관계를 추출하라.

반드시 다음 JSON 스키마로만 엄격히 응답하라:
{
  "systems": ["시스템명1", "시스템명2"],
  "teams": ["담당부서/팀1", "관련팀2"],
  "relations": [
    {
      "source_type": "Decision",
      "rel_type": "AFFECTS" | "DEPRECATES" | "SUPERSEDES" | "DEPENDS_ON" | "DECIDED_BY" | "IMPLEMENTS",
      "target_type": "System" | "Team" | "Policy" | "Decision",
      "target_name": "대상 엔티티명",
      "description": "영향 또는 사유 간략 설명"
    }
  ]
}
추출할 수 있는 시스템이나 명시적 관계가 없다면 빈 배열로 응답하라."""

GRAPH_EXTRACTION_USER = """[문서 제목]: {title}
[문서 본문]:
{content}

JSON 응답:"""

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


def _parse_json(text: str) -> dict:
    cleaned = text.strip()
    match = _JSON_BLOCK.search(cleaned)
    if match:
        cleaned = match.group(1).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            return json.loads(cleaned[start_idx : end_idx + 1])
        raise


_TEAM_PATTERN = re.compile(r"([가-힣A-Za-z0-9]{2,10}팀)")
_SYSTEM_PATTERN = re.compile(r"([가-힣A-Za-z0-9]{2,15}(?:시스템|게이트웨이|배치|서버|플랫폼|서비스|모듈))")


class DecisionGraphExtractor:
    def __init__(self, generator: CompletionGenerator):
        self._generator = generator

    async def extract_graph_elements(
        self,
        doc_id: int,
        title: str,
        content: str,
        author_team: str | None = None,
        max_chars: int = 3000,
    ) -> Dict[str, Any]:
        """문서로부터 지식 그래프 노드 및 엣지를 추출한다."""
        if not content or not content.strip():
            return {"systems": [], "teams": [], "relations": []}

        clipped = content[:max_chars]
        prompt = GRAPH_EXTRACTION_USER.format(title=title, content=clipped)

        systems: list[str] = []
        teams: list[str] = []
        relations: list[dict] = []

        try:
            if hasattr(self._generator, "complete"):
                resp_text = await self._generator.complete(GRAPH_EXTRACTION_SYSTEM, prompt, max_tokens=1500)
            else:
                resp_text = "{}"

            data = _parse_json(resp_text)
            if isinstance(data, dict):
                systems = [str(s).strip() for s in data.get("systems", []) if str(s).strip()]
                teams = [str(t).strip() for t in data.get("teams", []) if str(t).strip()]
                for r in data.get("relations", []):
                    if isinstance(r, dict) and r.get("rel_type") and r.get("target_name"):
                        rel_type = str(r["rel_type"]).upper()
                        target_type = str(r.get("target_type", "System"))
                        # LLM 출력은 신뢰하지 않는다(Cypher Injection). 허용 목록 밖의
                        # 값은 그래프 동기화 단계까지 흘러가면 안 되므로 여기서 버린다
                        if rel_type not in REL_TYPES or target_type not in NODE_LABELS:
                            log.warning(
                                "LLM이 허용 목록 밖의 관계·라벨을 반환해 건너뜀",
                                doc_id=doc_id, rel_type=rel_type, target_type=target_type,
                            )
                            continue
                        relations.append(
                            {
                                "source_type": "Decision",
                                "source_id": doc_id,
                                "source_title": title,
                                "rel_type": rel_type,
                                "target_type": target_type,
                                "target_name": str(r["target_name"]).strip(),
                                "description": str(r.get("description", "")).strip(),
                            }
                        )
        except Exception as e:
            log.warning("지식 그래프 엔티티/관계 LLM 추출 실패 (휴리스틱 정규식 폴백 적용)", doc_id=doc_id, error=str(e))

        # ─── 정규식 및 휴리스틱 폴백 ───────────────────────────
        full_text = f"{title}\n{clipped}"
        regex_teams = list(set(_TEAM_PATTERN.findall(full_text)))
        regex_systems = list(set(_SYSTEM_PATTERN.findall(full_text)))

        for t in regex_teams:
            if t not in teams:
                teams.append(t)
        for s in regex_systems:
            if s not in systems:
                systems.append(s)

        if author_team and author_team not in teams:
            teams.append(author_team)

        # 시스템 영향/종속 관계 자동 보정
        if not relations:
            for s in systems:
                rel = "DEPENDS_ON" if "종속" in full_text else "AFFECTS"
                relations.append(
                    {
                        "source_type": "Decision",
                        "source_id": doc_id,
                        "source_title": title,
                        "rel_type": rel,
                        "target_type": "System",
                        "target_name": s,
                        "description": "본문 언급 시스템 연관",
                    }
                )

        if author_team and not any(r.get("rel_type") == "DECIDED_BY" for r in relations):
            relations.append(
                {
                    "source_type": "Decision",
                    "source_id": doc_id,
                    "source_title": title,
                    "rel_type": "DECIDED_BY",
                    "target_type": "Team",
                    "target_name": author_team,
                    "description": "작성/담당 부서",
                }
            )

        return {
            "systems": systems,
            "teams": teams,
            "relations": relations,
        }

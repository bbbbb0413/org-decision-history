"""AnswerState — AskService.ask() 안의 지역 변수들을 그래프 상태로 옮긴 것."""
from datetime import datetime
from typing import TypedDict

from app.auth_client import PermissionScope
from app.generation.models import AnswerStatus
from app.search.models import SearchHit


class AnswerState(TypedDict, total=False):
    user_id: int
    conversation_id: int
    question: str
    started_at: float

    # 보안 가드레일 (PII 마스킹 및 인젝션 방어)
    masked_question: str
    security_verdict: str  # "ALLOWED" | "BLOCKED"
    block_reason: str | None

    scope: PermissionScope
    rewritten_question: str

    # 질의 계획(plan_query): 갈래와 실제 검색 질의
    complexity: str  # "simple" | "complex" | "abstract"
    search_queries: list[str]

    summary: str | None
    recent_turns: list[str]

    hits: list[SearchHit]
    # 근거 판정 결과(judge_evidence). "pass" 면 답변 생성으로 간다
    evidence_verdict: str
    # 판정 경로(지표 evidence_verdict_total 의 outcome 과 같다)
    evidence_outcome: str
    evidence: list[SearchHit]
    sources: list[dict]

    # GraphRAG (지식 서브그래프 인과 컨텍스트 & DAG 시각화 데이터)
    subgraph_context: str | None
    subgraph_triples: list[str] | None
    subgraph_nodes: list[dict] | None
    subgraph_edges: list[dict] | None

    cached_answer: str | None

    answer_text: str
    status: AnswerStatus
    model_name: str | None
    resume_at: datetime | None
    suggestions: list[str]

    # 자가 비판(답변·근거 대조). 결과는 상태 표시에만 쓰고 재검색하지 않는다
    confidence: float
    missing: list[str]

    message_id: int
    finished: bool
    """조기 종료(근거 없음/캐시 히트/보안 차단) 경로가 이미 done 이벤트를 보냈으면 True.
    나머지 노드들이 중복으로 done을 보내지 않게 조건부 엣지에서 이 값을 본다."""

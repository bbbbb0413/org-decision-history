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

    # 질의 지능화 (Step 2: Complexity Router, HyDE, Decomposer)
    complexity: str  # "simple" | "complex" | "hyde"
    search_queries: list[str]

    summary: str | None
    recent_turns: list[str]

    hits: list[SearchHit]
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

    # 자가 비판 및 환각 검증 (Step 3: Self-Critique & Refine Loop)
    confidence: float
    missing: list[str]
    critique_attempts: int  # 무한 루프 방지용 (최대 1회 재검색)

    message_id: int
    finished: bool
    """조기 종료(근거 없음/캐시 히트/보안 차단) 경로가 이미 done 이벤트를 보냈으면 True.
    나머지 노드들이 중복으로 done을 보내지 않게 조건부 엣지에서 이 값을 본다."""

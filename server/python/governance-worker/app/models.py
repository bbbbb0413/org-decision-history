"""거버넌스 및 상충 감지 관련 모델."""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SearchHit:
    document_id: int
    document_version: int
    chunk_no: int
    content: str | None
    section_path: str | None
    chunk_tag: str | None
    score: float
    vector_similarity: float | None = None


@dataclass(frozen=True)
class ConflictDetectionResult:
    source_doc_id: int
    target_doc_id: int
    conflict_type: str
    severity_score: float
    source_assertion: str
    target_assertion: str
    reason: str
    impacted_teams: List[str]
    suggested_action: str

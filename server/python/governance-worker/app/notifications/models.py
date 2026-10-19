from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

from app.severity import display_label


@dataclass(frozen=True)
class ConflictNotificationPayload:
    conflict_id: int
    source_doc_id: int
    source_doc_title: str
    source_clause: str
    target_doc_id: int
    target_doc_title: str
    target_clause: str
    conflict_type: str
    severity_score: float
    severity_level: str  # 기록할 때 정한 등급(app/severity.py). 여기서 다시 계산하지 않는다
    reasoning: str
    affected_teams: List[str]
    recommended_action: str
    frontend_base_url: str = "http://localhost:5173"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def source_doc_url(self) -> str:
        return f"{self.frontend_base_url.rstrip('/')}/documents/{self.source_doc_id}"

    @property
    def target_doc_url(self) -> str:
        return f"{self.frontend_base_url.rstrip('/')}/documents/{self.target_doc_id}"

    @property
    def conflict_url(self) -> str:
        return f"{self.frontend_base_url.rstrip('/')}/governance/conflicts/{self.conflict_id}"

    @property
    def severity_label(self) -> str:
        return display_label(self.severity_level, self.severity_score)

    @property
    def is_critical(self) -> bool:
        return self.severity_level == "CRITICAL"

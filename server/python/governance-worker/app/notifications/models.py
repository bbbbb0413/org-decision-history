from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


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
        if self.severity_score >= 0.85:
            return f"🔴 High ({self.severity_score:.2f})"
        elif self.severity_score >= 0.70:
            return f"🟡 Medium ({self.severity_score:.2f})"
        return f"🟢 Low ({self.severity_score:.2f})"

"""답변 생성의 입출력 모델(AnswerChunk·AnswerStatus·AnswerRequest)."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.search.models import SearchHit


class AnswerStatus(str, Enum):
    OK = "OK"
    NO_EVIDENCE = "NO_EVIDENCE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"


@dataclass(frozen=True)
class AnswerChunk:
    text: str | None
    done: bool
    status: AnswerStatus | None = None
    model_name: str | None = None
    resume_at: datetime | None = None

    @staticmethod
    def delta(text: str) -> "AnswerChunk":
        return AnswerChunk(text=text, done=False)

    @staticmethod
    def finished(
        status: AnswerStatus, model_name: str | None, resume_at: datetime | None = None
    ) -> "AnswerChunk":
        return AnswerChunk(text=None, done=True, status=status, model_name=model_name, resume_at=resume_at)


@dataclass(frozen=True)
class AnswerRequest:
    question: str
    summary: str | None
    recent_turns: list[str]
    evidence: list[SearchHit]

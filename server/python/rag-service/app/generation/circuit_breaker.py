from datetime import datetime, timezone
from enum import Enum
import structlog

log = structlog.get_logger()


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenException(Exception):
    """서킷 브레이커가 OPEN 상태여서 호출을 즉시 차단함 (Fast-Fail)."""


class CircuitBreaker:
    """외부 LLM API의 연속 장애 시 빠른 실패(Fast-Fail)를 보장하는 서킷 브레이커."""

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout_seconds: float = 30.0,
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._opened_at: datetime | None = None

    @property
    def state(self) -> CircuitState:
        now = datetime.now(timezone.utc)
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = (now - self._opened_at).total_seconds()
            if elapsed >= self.reset_timeout_seconds:
                log.info(
                    "서킷 브레이커 HALF_OPEN 전환 (시험 호출 허용)",
                    elapsed_seconds=elapsed,
                )
                self._state = CircuitState.HALF_OPEN
        return self._state

    def can_call(self) -> bool:
        return self.state != CircuitState.OPEN

    def record_success(self) -> None:
        if self._state != CircuitState.CLOSED:
            log.info("서킷 브레이커 CLOSED 복구 (정상 호출 성공)")
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = None

    def record_failure(self, error: Exception | str | None = None) -> None:
        self._failure_count += 1
        now = datetime.now(timezone.utc)

        if self._state == CircuitState.HALF_OPEN or self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = now
            log.error(
                "서킷 브레이커 OPEN 차단 활성화 (Fast-Fail 모드)",
                failure_count=self._failure_count,
                threshold=self.failure_threshold,
                error=str(error) if error else None,
            )
        else:
            log.warning(
                "외부 호출 실패 기록",
                failure_count=self._failure_count,
                threshold=self.failure_threshold,
            )


__all__ = ["CircuitState", "CircuitBreakerOpenException", "CircuitBreaker"]

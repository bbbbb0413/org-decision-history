"""알림 채널 Protocol 및 인터페이스 정의."""
from typing import Protocol, runtime_checkable
from app.notifications.models import ConflictNotificationPayload


@runtime_checkable
class NotificationChannel(Protocol):
    @property
    def name(self) -> str:
        """채널 식별자 (예: slack, teams, email)."""
        ...

    def is_enabled(self) -> bool:
        """설정값 및 자격증명 유효성에 따라 채널 활성화 여부 반환."""
        ...

    async def send(self, payload: ConflictNotificationPayload) -> bool:
        """알림을 발송한다. 성공 시 True, 실패 시 False (예외는 내부에서 처리)."""
        ...

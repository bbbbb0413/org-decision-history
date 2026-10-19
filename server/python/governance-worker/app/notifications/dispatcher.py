"""모듈형 알림 디스패처 (Notification Dispatcher)."""
import asyncio
from typing import List, Dict
import structlog
from app.notifications.channels.base import NotificationChannel
from app.notifications.models import ConflictNotificationPayload

log = structlog.get_logger()


class NotificationDispatcher:
    def __init__(self, channels: List[NotificationChannel], min_severity: float):
        self._channels = list(channels)
        self._min_severity = min_severity

    @property
    def channels(self) -> List[NotificationChannel]:
        return list(self._channels)

    def get_active_channels(self) -> List[NotificationChannel]:
        return [c for c in self._channels if c.is_enabled()]

    async def dispatch(
        self,
        payload: ConflictNotificationPayload,
        extra_channels: List[NotificationChannel] | None = None,
    ) -> Dict[str, bool]:
        """활성화된 채널들에 병렬로 알림을 전송한다. 개별 채널 오류는 격리된다.

        extra_channels 는 이 디스패치 호출 한 번에만 쓰는 채널이다 —
        상충 문서의 팀에 맞춰 detector.py 가 그때그때 만들어 넘긴다. 생성자로 받은
        고정 채널 목록(self._channels, 전역 설정)은 그대로 유지되고 여기에 더해진다 —
        팀 채널 미설정 조직/문서에 대한 폴백이다.
        """
        if payload.severity_score < self._min_severity:
            log.debug(
                "상충 심각도 점수가 최소 임계치 미만이므로 알림 생략",
                score=payload.severity_score,
                min_severity=self._min_severity,
                conflict_id=payload.conflict_id,
            )
            return {}

        active = self.get_active_channels() + [c for c in (extra_channels or []) if c.is_enabled()]
        if not active:
            log.debug(
                "활성화된 알림 채널 없음 (모든 채널 OFF 상태)",
                conflict_id=payload.conflict_id,
            )
            return {}

        log.info(
            "활성화된 알림 채널로 상충 알림 병렬 발송 시작",
            conflict_id=payload.conflict_id,
            active_channels=[c.name for c in active],
        )

        async def _safe_send(channel: NotificationChannel) -> tuple[str, bool]:
            try:
                success = await channel.send(payload)
                return channel.name, success
            except Exception as e:
                log.warning("채널 알림 발송 중 예외 격리", channel=channel.name, error=str(e))
                return channel.name, False

        tasks = [_safe_send(c) for c in active]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return dict(results)

"""Slack Block Kit 웹훅 알림 채널."""
import httpx
import structlog
from app.notifications.models import ConflictNotificationPayload

log = structlog.get_logger()


class SlackNotificationChannel:
    def __init__(self, webhook_url: str, enabled: bool = True, timeout_seconds: float = 5.0):
        self._webhook_url = webhook_url.strip() if webhook_url else ""
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "slack"

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._webhook_url)

    def build_payload(self, payload: ConflictNotificationPayload) -> dict:
        teams_str = ", ".join(payload.affected_teams) if payload.affected_teams else "전사/공통"
        return {
            "text": f"⚠️ [정책 상충 감지] '{payload.source_doc_title}' 문서가 기존 규정과 충돌합니다.",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "⚠️ 정책/의사결정 상충(Conflict) 감지 리포트",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*신규 문서:*\n<{payload.source_doc_url}|{payload.source_doc_title}>",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*충돌 대상 규정:*\n<{payload.target_doc_url}|{payload.target_doc_title}>",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*심각도:*\n{payload.severity_label}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*영향 부서:*\n{teams_str}",
                        },
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*상충 사유:*\n{payload.reasoning}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*신규 조항:*\n> {payload.source_clause}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*기존 조항:*\n> {payload.target_clause}",
                        },
                    ],
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "충돌 리포트 확인 및 조치",
                                "emoji": True,
                            },
                            "url": payload.conflict_url,
                            "style": "danger" if payload.is_critical else "primary",
                        }
                    ],
                },
            ],
        }

    async def send(self, payload: ConflictNotificationPayload) -> bool:
        if not self.is_enabled():
            return False

        body = self.build_payload(payload)

        # Mock / Simulation mode check
        if self._webhook_url.lower() in ("mock", "[mock]", "simulate", "http://mock-slack"):
            log.info(
                "Slack 웹훅 발송 시뮬레이션 (Mock)",
                conflict_id=payload.conflict_id,
                channel="slack",
                payload=body,
            )
            return True

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(self._webhook_url, json=body)
                if res.status_code == 200:
                    log.info("Slack 웹훅 발송 성공", conflict_id=payload.conflict_id)
                    return True
                else:
                    log.warning(
                        "Slack 웹훅 응답 오류",
                        status_code=res.status_code,
                        response=res.text[:200],
                        conflict_id=payload.conflict_id,
                    )
                    return False
        except Exception as e:
            log.warning("Slack 웹훅 전송 실패", error=str(e), conflict_id=payload.conflict_id)
            return False

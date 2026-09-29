"""Microsoft Teams 웹훅 알림 채널."""
import httpx
import structlog
from app.notifications.models import ConflictNotificationPayload

log = structlog.get_logger()


class TeamsNotificationChannel:
    def __init__(self, webhook_url: str, enabled: bool = True, timeout_seconds: float = 5.0):
        self._webhook_url = webhook_url.strip() if webhook_url else ""
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "teams"

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._webhook_url)

    def build_payload(self, payload: ConflictNotificationPayload) -> dict:
        teams_str = ", ".join(payload.affected_teams) if payload.affected_teams else "전사/공통"
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "D9383A" if payload.severity_score >= 0.85 else "FF9900",
            "summary": f"⚠️ [정책 상충] {payload.source_doc_title}",
            "sections": [
                {
                    "activityTitle": "⚠️ 정책/의사결정 상충(Conflict) 감지 리포트",
                    "activitySubtitle": "신규 문서가 기존 승인 규정과 상충됩니다.",
                    "facts": [
                        {"name": "신규 문서", "value": f"[{payload.source_doc_title}]({payload.source_doc_url})"},
                        {"name": "충돌 대상 규정", "value": f"[{payload.target_doc_title}]({payload.target_doc_url})"},
                        {"name": "상충 유형", "value": payload.conflict_type},
                        {"name": "심각도", "value": payload.severity_label},
                        {"name": "영향 부서", "value": teams_str},
                        {"name": "상충 사유", "value": payload.reasoning},
                        {"name": "신규 조항", "value": payload.source_clause},
                        {"name": "기존 조항", "value": payload.target_clause},
                        {"name": "권고 조치", "value": payload.recommended_action or "담당 부서 검토 필요"},
                    ],
                    "markdown": True,
                }
            ],
            "potentialAction": [
                {
                    "@type": "OpenUri",
                    "name": "충돌 리포트 확인 및 조치",
                    "targets": [{"os": "default", "uri": payload.conflict_url}],
                }
            ],
        }

    async def send(self, payload: ConflictNotificationPayload) -> bool:
        if not self.is_enabled():
            return False

        body = self.build_payload(payload)

        # Mock / Simulation mode check
        if self._webhook_url.lower() in ("mock", "[mock]", "simulate", "http://mock-teams"):
            log.info(
                "Teams 웹훅 발송 시뮬레이션 (Mock)",
                conflict_id=payload.conflict_id,
                channel="teams",
                payload=body,
            )
            return True

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                res = await client.post(self._webhook_url, json=body)
                if res.status_code in (200, 202):
                    log.info("Teams 웹훅 발송 성공", conflict_id=payload.conflict_id)
                    return True
                else:
                    log.warning(
                        "Teams 웹훅 응답 오류",
                        status_code=res.status_code,
                        response=res.text[:200],
                        conflict_id=payload.conflict_id,
                    )
                    return False
        except Exception as e:
            log.warning("Teams 웹훅 전송 실패", error=str(e), conflict_id=payload.conflict_id)
            return False

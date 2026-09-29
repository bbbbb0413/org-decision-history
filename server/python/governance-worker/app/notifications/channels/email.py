"""이메일(SMTP) 알림 채널."""
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import structlog
from app.notifications.models import ConflictNotificationPayload

log = structlog.get_logger()


class EmailNotificationChannel:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_use_tls: bool = True,
        email_from: str = "noreply@company.com",
        email_to: str = "",
        enabled: bool = True,
        timeout_seconds: float = 10.0,
    ):
        self._smtp_host = smtp_host.strip() if smtp_host else ""
        self._smtp_port = smtp_port
        self._smtp_username = smtp_username.strip() if smtp_username else ""
        self._smtp_password = smtp_password.strip() if smtp_password else ""
        self._smtp_use_tls = smtp_use_tls
        self._email_from = email_from.strip() if email_from else "noreply@company.com"
        self._email_to_list = [addr.strip() for addr in email_to.split(",") if addr.strip()] if email_to else []
        self._enabled = enabled
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "email"

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._smtp_host) and len(self._email_to_list) > 0

    def build_email_contents(self, payload: ConflictNotificationPayload) -> tuple[str, str]:
        """(plain_text, html_content) 반환."""
        teams_str = ", ".join(payload.affected_teams) if payload.affected_teams else "전사/공통"

        text_content = f"""[사내 정책/의사결정 상충(Conflict) 감지 리포트]

신규 등록/개정된 문서가 기존 사규 및 승인된 의사결정과 상충됩니다.

- 신규 문서: {payload.source_doc_title} ({payload.source_doc_url})
- 충돌 대상 규정: {payload.target_doc_title} ({payload.target_doc_url})
- 심각도: {payload.severity_label}
- 상충 유형: {payload.conflict_type}
- 영향 부서: {teams_str}

[상충 사유]
{payload.reasoning}

[신규 조항]
{payload.source_clause}

[기존 조항]
{payload.target_clause}

[권고 조치]
{payload.recommended_action or '관련 부서 사전 협의 및 심의 필요'}

상충 리포트 확인 및 조치 바로가기:
{payload.conflict_url}
"""

        html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1f2937; background-color: #f9fafb; margin: 0; padding: 20px; }}
  .container {{ max-width: 640px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e5e7eb; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
  .header {{ background-color: #b91c1c; color: #ffffff; padding: 20px 24px; }}
  .header h2 {{ margin: 0; font-size: 20px; font-weight: 700; }}
  .content {{ padding: 24px; }}
  .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: 600; font-size: 13px; background-color: #fee2e2; color: #991b1b; }}
  .meta-table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  .meta-table th {{ text-align: left; padding: 8px 12px; background-color: #f3f4f6; color: #4b5563; font-size: 13px; width: 120px; border-bottom: 1px solid #e5e7eb; }}
  .meta-table td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-size: 14px; }}
  .box {{ background-color: #f8fafc; border-left: 4px solid #3b82f6; padding: 12px 16px; margin: 16px 0; border-radius: 0 4px 4px 0; }}
  .btn {{ display: inline-block; background-color: #2563eb; color: #ffffff !important; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: 600; margin-top: 16px; }}
  .footer {{ background-color: #f9fafb; padding: 16px 24px; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h2>⚠️ 사내 정책/의사결정 상충(Conflict) 감지 리포트</h2>
  </div>
  <div class="content">
    <p>신규 등록/개정된 문서가 기존 사규 및 의사결정과 상충하여 거버넌스 알림을 발송합니다.</p>
    
    <table class="meta-table">
      <tr>
        <th>신규 문서</th>
        <td><a href="{payload.source_doc_url}" style="color:#2563eb; font-weight:600;">{payload.source_doc_title}</a></td>
      </tr>
      <tr>
        <th>충돌 대상 규정</th>
        <td><a href="{payload.target_doc_url}" style="color:#2563eb; font-weight:600;">{payload.target_doc_title}</a></td>
      </tr>
      <tr>
        <th>심각도 / 유형</th>
        <td><span class="badge">{payload.severity_label}</span> &nbsp; <code>{payload.conflict_type}</code></td>
      </tr>
      <tr>
        <th>영향 부서</th>
        <td><strong>{teams_str}</strong></td>
      </tr>
      <tr>
        <th>권고 조치</th>
        <td>{payload.recommended_action or '관련 부서 사전 협의 및 예외 승인 검토'}</td>
      </tr>
    </table>

    <div class="box">
      <strong>📌 상충 사유:</strong><br>
      {payload.reasoning}
    </div>

    <div style="background-color:#fef2f2; border-left: 4px solid #ef4444; padding: 12px 16px; margin: 16px 0; border-radius: 0 4px 4px 0;">
      <div style="margin-bottom: 8px;"><strong>[신규 조항]:</strong> {payload.source_clause}</div>
      <div><strong>[기존 조항]:</strong> {payload.target_clause}</div>
    </div>

    <div style="text-align: center;">
      <a href="{payload.conflict_url}" class="btn">상충 내역 확인 및 조치(해결/예외)</a>
    </div>
  </div>
  <div class="footer">
    본 메일은 사내 거버넌스 의사결정 이력 관리 시스템에서 자동 발송되었습니다.
  </div>
</div>
</body>
</html>
"""
        return text_content, html_content

    def _send_sync(self, msg: MIMEMultipart) -> None:
        if self._smtp_use_tls:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as server:
                server.starttls()
                if self._smtp_username and self._smtp_password:
                    server.login(self._smtp_username, self._smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=self._timeout) as server:
                if self._smtp_username and self._smtp_password:
                    server.login(self._smtp_username, self._smtp_password)
                server.send_message(msg)

    async def send(self, payload: ConflictNotificationPayload) -> bool:
        if not self.is_enabled():
            return False

        # Mock / Simulation mode check
        if self._smtp_host.lower() in ("mock", "[mock]", "simulate", "localhost.mock"):
            log.info(
                "Email 알림 발송 시뮬레이션 (Mock)",
                conflict_id=payload.conflict_id,
                recipients=self._email_to_list,
                from_email=self._email_from,
                subject=f"[정책 상충 감지] '{payload.source_doc_title}' 상충 리포트",
            )
            return True

        text_content, html_content = self.build_email_contents(payload)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚠️ [정책 상충 감지] '{payload.source_doc_title}' 사규 상충 리포트"
        msg["From"] = self._email_from
        msg["To"] = ", ".join(self._email_to_list)

        part1 = MIMEText(text_content, "plain", "utf-8")
        part2 = MIMEText(html_content, "html", "utf-8")
        msg.attach(part1)
        msg.attach(part2)

        try:
            await asyncio.to_thread(self._send_sync, msg)
            log.info("Email 알림 발송 성공", conflict_id=payload.conflict_id, recipients=self._email_to_list)
            return True
        except Exception as e:
            log.warning("Email 알림 발송 실패", error=str(e), conflict_id=payload.conflict_id)
            return False

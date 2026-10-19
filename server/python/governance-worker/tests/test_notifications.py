import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.notifications.models import ConflictNotificationPayload
from app.notifications.channels.slack import SlackNotificationChannel
from app.notifications.channels.teams import TeamsNotificationChannel
from app.notifications.channels.email import EmailNotificationChannel
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.factory import create_notification_dispatcher
from app.settings import Settings
from app.severity import SeverityBands


def _create_sample_payload(severity: float = 0.92) -> ConflictNotificationPayload:
    return ConflictNotificationPayload(
        conflict_id=101,
        source_doc_id=17,
        source_doc_title="2026 전사 근무제도 개정안",
        source_clause="전 임직원은 주 5일 전원 본사 사무실로 출근해야 한다.",
        target_doc_id=16,
        target_doc_title="원격근무 지침",
        target_clause="전 임직원은 주 5일 100% 재택근무를 실시한다.",
        conflict_type="DIRECT_CONTRADICTION",
        severity_score=severity,
        severity_level=SeverityBands().level_of(severity),
        reasoning="신규 문서는 전원 출근을 요구하나 기존 규정은 재택근무를 규정함.",
        affected_teams=["인사팀", "운영팀"],
        recommended_action="인사위원회 심의 필요",
        frontend_base_url="http://localhost:5173",
    )


# ─── 1. Slack 채널 테스트 ──────────────────────────────────

def test_slack_channel_enabled_conditions():
    c1 = SlackNotificationChannel(webhook_url="", enabled=True)
    assert c1.is_enabled() is False

    c2 = SlackNotificationChannel(webhook_url="https://hooks.slack.com/services/xxx", enabled=False)
    assert c2.is_enabled() is False

    c3 = SlackNotificationChannel(webhook_url="https://hooks.slack.com/services/xxx", enabled=True)
    assert c3.is_enabled() is True


def test_slack_block_kit_payload_structure():
    channel = SlackNotificationChannel(webhook_url="https://hooks.slack.com/services/xxx", enabled=True)
    payload = _create_sample_payload()
    body = channel.build_payload(payload)

    assert "blocks" in body
    blocks = body["blocks"]
    assert blocks[0]["type"] == "header"
    assert "정책/의사결정 상충" in blocks[0]["text"]["text"]

    # 4 fields
    fields = blocks[1]["fields"]
    assert len(fields) == 4
    assert "신규 문서" in fields[0]["text"]
    assert "http://localhost:5173/documents/17" in fields[0]["text"]
    assert "충돌 대상 규정" in fields[1]["text"]
    assert "http://localhost:5173/documents/16" in fields[1]["text"]
    # 대시보드 배지와 같은 이름을 쓴다
    assert "🔴 치명적 (0.92)" in fields[2]["text"]
    assert "인사팀, 운영팀" in fields[3]["text"]

    # Action button
    action_element = blocks[4]["elements"][0]
    assert action_element["type"] == "button"
    assert action_element["url"] == "http://localhost:5173/governance/conflicts/101"


@pytest.mark.asyncio
async def test_slack_send_mock_simulation():
    channel = SlackNotificationChannel(webhook_url="mock", enabled=True)
    payload = _create_sample_payload()
    res = await channel.send(payload)
    assert res is True


@pytest.mark.asyncio
async def test_slack_send_http_success_and_failure():
    channel = SlackNotificationChannel(webhook_url="https://hooks.slack.com/services/xxx", enabled=True)
    payload = _create_sample_payload()

    # Success (200)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        assert await channel.send(payload) is True

    # Server Error (500)
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        assert await channel.send(payload) is False

    # Network Exception
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.ConnectError("Connection refused")
        assert await channel.send(payload) is False


# ─── 2. Teams 채널 테스트 ──────────────────────────────────

def test_teams_channel_enabled_conditions():
    c1 = TeamsNotificationChannel(webhook_url="", enabled=True)
    assert c1.is_enabled() is False

    c2 = TeamsNotificationChannel(webhook_url="https://outlook.office.com/webhook/xxx", enabled=False)
    assert c2.is_enabled() is False

    c3 = TeamsNotificationChannel(webhook_url="https://outlook.office.com/webhook/xxx", enabled=True)
    assert c3.is_enabled() is True


def test_teams_message_card_payload_structure():
    channel = TeamsNotificationChannel(webhook_url="https://outlook.office.com/webhook/xxx", enabled=True)
    payload = _create_sample_payload()
    body = channel.build_payload(payload)

    assert body["@type"] == "MessageCard"
    facts = body["sections"][0]["facts"]
    fact_names = [f["name"] for f in facts]
    assert "신규 문서" in fact_names
    assert "충돌 대상 규정" in fact_names
    assert "심각도" in fact_names
    assert "영향 부서" in fact_names

    potential_action = body["potentialAction"][0]
    assert potential_action["@type"] == "OpenUri"
    assert potential_action["targets"][0]["uri"] == "http://localhost:5173/governance/conflicts/101"


@pytest.mark.asyncio
async def test_teams_send_mock_simulation():
    channel = TeamsNotificationChannel(webhook_url="mock", enabled=True)
    payload = _create_sample_payload()
    assert await channel.send(payload) is True


# ─── 3. Email 채널 테스트 ──────────────────────────────────

def test_email_channel_enabled_conditions():
    # Empty host -> disabled
    c1 = EmailNotificationChannel(smtp_host="", email_to="admin@company.com", enabled=True)
    assert c1.is_enabled() is False

    # Empty recipients -> disabled
    c2 = EmailNotificationChannel(smtp_host="smtp.gmail.com", email_to="", enabled=True)
    assert c2.is_enabled() is False

    # Enabled flag False -> disabled
    c3 = EmailNotificationChannel(smtp_host="smtp.gmail.com", email_to="admin@company.com", enabled=False)
    assert c3.is_enabled() is False

    # Fully configured -> enabled
    c4 = EmailNotificationChannel(smtp_host="smtp.gmail.com", email_to="a@co.com, b@co.com", enabled=True)
    assert c4.is_enabled() is True
    assert c4._email_to_list == ["a@co.com", "b@co.com"]


def test_email_contents_generation():
    channel = EmailNotificationChannel(smtp_host="smtp.gmail.com", email_to="admin@company.com", enabled=True)
    payload = _create_sample_payload()
    text_c, html_c = channel.build_email_contents(payload)

    assert "[사내 정책/의사결정 상충(Conflict) 감지 리포트]" in text_c
    assert "http://localhost:5173/governance/conflicts/101" in text_c

    assert "<!DOCTYPE html>" in html_c
    assert "2026 전사 근무제도 개정안" in html_c
    assert "원격근무 지침" in html_c
    assert "인사팀, 운영팀" in html_c


@pytest.mark.asyncio
async def test_email_send_mock_simulation():
    channel = EmailNotificationChannel(smtp_host="mock", email_to="admin@company.com", enabled=True)
    payload = _create_sample_payload()
    assert await channel.send(payload) is True


@pytest.mark.asyncio
async def test_email_send_smtp_dispatch():
    channel = EmailNotificationChannel(
        smtp_host="smtp.company.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="pwd",
        smtp_use_tls=True,
        email_to="governance@company.com",
        enabled=True,
    )
    payload = _create_sample_payload()

    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = mock_server

        assert await channel.send(payload) is True
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pwd")
        mock_server.send_message.assert_called_once()


# ─── 4. NotificationDispatcher 및 모듈화 구조 검증 ─────────────

@pytest.mark.asyncio
async def test_dispatcher_filters_only_active_channels():
    c_slack = SlackNotificationChannel(webhook_url="mock", enabled=True)
    c_teams = TeamsNotificationChannel(webhook_url="", enabled=True)       # Disabled due to empty URL
    c_email = EmailNotificationChannel(smtp_host="mock", email_to="a@b.com", enabled=True)

    dispatcher = NotificationDispatcher(channels=[c_slack, c_teams, c_email], min_severity=0.70)
    active = dispatcher.get_active_channels()

    assert len(active) == 2
    assert set(c.name for c in active) == {"slack", "email"}


@pytest.mark.asyncio
async def test_dispatcher_min_severity_skipping():
    c_slack = SlackNotificationChannel(webhook_url="mock", enabled=True)
    dispatcher = NotificationDispatcher(channels=[c_slack], min_severity=0.70)

    low_severity_payload = _create_sample_payload(severity=0.55)
    results = await dispatcher.dispatch(low_severity_payload)
    assert results == {}  # Skipped


@pytest.mark.asyncio
async def test_dispatcher_concurrent_dispatch_and_fault_isolation():
    c_slack = SlackNotificationChannel(webhook_url="mock", enabled=True)
    c_teams = TeamsNotificationChannel(webhook_url="https://teams.com/webhook", enabled=True)
    c_email = EmailNotificationChannel(smtp_host="mock", email_to="admin@company.com", enabled=True)

    # Make teams fail with exception
    with patch.object(c_teams, "send", new_callable=AsyncMock) as mock_teams_send:
        mock_teams_send.side_effect = RuntimeError("Teams connection crashed!")

        dispatcher = NotificationDispatcher(channels=[c_slack, c_teams, c_email], min_severity=0.70)
        results = await dispatcher.dispatch(_create_sample_payload(severity=0.88))

        assert results["slack"] is True
        assert results["teams"] is False  # Exception isolated, returns False
        assert results["email"] is True


def test_factory_creation_with_settings():
    settings = Settings(
        notification_enabled=True,
        slack_webhook_enabled=True,
        slack_webhook_url="mock",
        teams_webhook_enabled=False,
        email_notification_enabled=True,
        smtp_host="mock",
        email_to="governance@company.com",
    )
    dispatcher = create_notification_dispatcher(settings)
    active_names = [c.name for c in dispatcher.get_active_channels()]
    assert "slack" in active_names
    assert "email" in active_names
    assert "teams" not in active_names


# ─── 심각도 등급 (app/severity.py) ─────────────────────────


def test_severity_bands_levels():
    bands = SeverityBands()
    assert bands.level_of(0.92) == "CRITICAL"
    assert bands.level_of(0.85) == "CRITICAL"
    assert bands.level_of(0.84) == "HIGH"
    assert bands.level_of(0.70) == "HIGH"
    assert bands.level_of(0.69) == "MEDIUM"


def test_severity_bands_reject_inverted_bounds():
    with pytest.raises(ValueError):
        SeverityBands(critical_min=0.7, high_min=0.85)


def test_payload_label_uses_stored_level_not_score():
    """알림은 기록할 때 정한 등급을 보여준다. 점수로 다시 계산하면 구간을 바꾼 뒤 DB·화면과 어긋난다."""
    payload = _create_sample_payload(severity=0.92)
    stored_as_high = ConflictNotificationPayload(**{**payload.__dict__, "severity_level": "HIGH"})
    assert stored_as_high.severity_label == "🟡 높음 (0.92)"
    assert stored_as_high.is_critical is False


def test_settings_reject_notification_threshold_below_record_threshold():
    """기록되지 않은 상충에는 알림도 없다. 알림 기준이 더 낮으면 설정의 뜻과 동작이 어긋난다."""
    with pytest.raises(ValueError):
        Settings(conflict_min_severity=0.8, notification_min_severity=0.7)
    # 알림만 더 엄격하게 하는 것은 된다
    Settings(conflict_min_severity=0.7, notification_min_severity=0.9)


def test_settings_reject_invalid_bands():
    with pytest.raises(ValueError):
        Settings(severity_critical_min=0.6, severity_high_min=0.7)

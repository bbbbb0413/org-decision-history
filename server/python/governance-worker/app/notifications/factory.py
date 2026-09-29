"""설정값 기반 알림 디스패처 팩토리."""
from app.notifications.channels.slack import SlackNotificationChannel
from app.notifications.channels.teams import TeamsNotificationChannel
from app.notifications.channels.email import EmailNotificationChannel
from app.notifications.dispatcher import NotificationDispatcher
from app.settings import Settings


def create_notification_dispatcher(settings: Settings) -> NotificationDispatcher:
    channels = [
        SlackNotificationChannel(
            webhook_url=settings.slack_webhook_url,
            enabled=settings.notification_enabled and settings.slack_webhook_enabled,
        ),
        TeamsNotificationChannel(
            webhook_url=settings.teams_webhook_url,
            enabled=settings.notification_enabled and settings.teams_webhook_enabled,
        ),
        EmailNotificationChannel(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_username=settings.smtp_username,
            smtp_password=settings.smtp_password,
            smtp_use_tls=settings.smtp_use_tls,
            email_from=settings.email_from,
            email_to=settings.email_to,
            enabled=settings.notification_enabled and settings.email_notification_enabled,
        ),
    ]
    return NotificationDispatcher(
        channels=channels,
        min_severity=settings.notification_min_severity,
    )

"""거버넌스 워커(governance-worker) 환경설정.

Postgres·Redis·Qdrant·임베딩(Gemini) 설정은 history_shared.config.ServiceSettings 에서 물려받는다.
"""
from history_shared.config import ServiceSettings
from history_shared.events import STREAM_INDEX_EVENTS
from pydantic_settings import SettingsConfigDict


class Settings(ServiceSettings):
    # 로컬에서 직접 실행할 때는 작업 디렉터리의 .env 도 읽는다. 컨테이너는 compose 가 환경변수로 넣는다.
    model_config = SettingsConfigDict(
        env_prefix="", case_sensitive=False, extra="ignore", env_file=".env", env_file_encoding="utf-8"
    )

    # Redis Stream. index-worker가 색인을 끝내야 본문(indexing.document_chunk)을 읽을 수 있어
    # stream:document-events가 아니라 stream:index-events(DOCUMENT_INDEXED)를 구독한다
    index_events_stream: str = STREAM_INDEX_EVENTS
    consumer_group: str = "governance-worker-group"
    consumer_name: str = ""  # 비우면 호스트 이름으로 만든다
    stream_claim_min_idle_ms: int = 60_000
    stream_max_deliveries: int = 5

    # LLM (Groq)
    groq_api_key: str = ""
    groq_endpoint: str = "https://api.groq.com"
    primary_model: str = "openai/gpt-oss-120b"
    fallback_model: str = "openai/gpt-oss-20b"

    # 알림 (Notification)
    notification_enabled: bool = True
    frontend_base_url: str = "http://localhost:5173"
    notification_min_severity: float = 0.70

    # Slack Webhook
    slack_webhook_enabled: bool = False
    slack_webhook_url: str = ""

    # MS Teams Webhook
    teams_webhook_enabled: bool = False
    teams_webhook_url: str = ""

    # Email Notification (SMTP)
    email_notification_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = "noreply@company.com"
    email_to: str = ""

    # Neo4j Graph DB
    neo4j_enabled: bool = True
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"


settings = Settings()

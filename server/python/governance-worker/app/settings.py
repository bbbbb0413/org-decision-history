"""거버넌스 워커(governance-worker) 환경설정.

Postgres·Redis·Qdrant·임베딩(Gemini) 설정은 history_shared.config.ServiceSettings 에서 물려받는다.
"""
from history_shared.config import ServiceSettings
from history_shared.events import STREAM_INDEX_EVENTS
from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from app.severity import SeverityBands


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

    # 상충 심각도. 기록 기준과 알림 기준은 따로 둔다 — 알림을 줄이려고 기준을 올렸을 때 기록까지
    # 사라지면 안 된다. 등급 구간은 기록할 때 등급을 정하는 데 쓴다(app/severity.py)
    conflict_min_severity: float = 0.70
    severity_critical_min: float = 0.85
    severity_high_min: float = 0.70

    # 조항 추출. 원문(색인 청크)을 이 글자 수 이하 창으로 나눠 창마다 LLM 을 한 번 부른다.
    # 창 수와 조항 수에 상한을 둬 긴 문서 하나가 LLM 호출(조항 수 × 후보 5개)을 폭증시키지 않게 한다.
    # 상한을 넘는 뒷부분은 검사하지 않고 로그를 남긴다
    assertion_window_chars: int = 4000
    assertion_max_windows: int = 5
    assertion_max_count: int = 10

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

    @model_validator(mode="after")
    def _check_severity(self) -> "Settings":
        self.severity_bands()  # 구간이 잘못됐으면 여기서 기동이 실패한다
        for name in ("assertion_window_chars", "assertion_max_windows", "assertion_max_count"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} 는 1 이상이어야 한다: {getattr(self, name)}")
        if not 0.0 <= self.conflict_min_severity <= 1.0:
            raise ValueError(f"conflict_min_severity 는 0~1 이어야 한다: {self.conflict_min_severity}")
        # 기록되지 않은 상충에는 보낼 알림도 없다. 알림 기준이 더 낮으면 설정이 뜻하는 바와 동작이 어긋난다
        if self.notification_min_severity < self.conflict_min_severity:
            raise ValueError(
                f"notification_min_severity({self.notification_min_severity}) 는 "
                f"conflict_min_severity({self.conflict_min_severity}) 보다 낮을 수 없다"
            )
        return self

    def severity_bands(self) -> SeverityBands:
        return SeverityBands(critical_min=self.severity_critical_min, high_min=self.severity_high_min)


settings = Settings()

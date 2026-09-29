"""서비스 공통 설정. 환경변수 이름은 필드 이름과 같다(대소문자 무시).

서비스는 필요한 묶음을 상속해 자기 설정을 만든다.

    class Settings(ServiceSettings):
        kafka_bootstrap_servers: str = "localhost:9092"
"""
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")


class PostgresSettings(BaseSettings):
    model_config = _CONFIG

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "knowledge"
    postgres_user: str = "history"
    postgres_password: str = ""

    @property
    def postgres_dsn(self) -> str:
        # 비밀번호에 @ : / 같은 문자가 있어도 DSN 이 깨지지 않게 인코딩한다
        return (
            f"postgresql://{quote(self.postgres_user, safe='')}:{quote(self.postgres_password, safe='')}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class RedisSettings(BaseSettings):
    model_config = _CONFIG

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""


class QdrantSettings(BaseSettings):
    model_config = _CONFIG

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "document_chunks"


class EmbeddingSettings(BaseSettings):
    """contracts/indexing/README.md §10. 색인과 검색이 같은 모델·차원을 써야 벡터 공간이 맞는다."""

    model_config = _CONFIG

    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_endpoint: str = "https://generativelanguage.googleapis.com"
    embedding_dimension: int = 768
    # Gemini 무료 티어 한도
    embedding_requests_per_minute: int = 100
    embedding_tokens_per_minute: int = 30_000
    embedding_batch_size: int = 20
    embedding_timeout_seconds: float = 30.0

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key.strip())


class MetricsSettings(BaseSettings):
    """Prometheus 노출.

    HTTP 앱이 있는 워커(index-worker)는 자기 포트의 `/actuator/prometheus` 로 내므로
    이 포트를 쓰지 않는다. HTTP 앱이 없는 워커(governance-worker·rag-service)만
    `history_shared.metrics.start_metrics_server` 로 이 포트에 전용 서버를 띄운다.
    """

    model_config = _CONFIG

    # 9000 은 MinIO 가 쓰는 번호라 헷갈리지 않게 피한다(컨테이너가 달라 충돌은 없지만)
    metrics_port: int = 9100
    metrics_enabled: bool = True


class ServiceSettings(PostgresSettings, RedisSettings, QdrantSettings, EmbeddingSettings, MetricsSettings):
    """워커 대부분이 쓰는 묶음."""

    model_config = _CONFIG

"""환경변수 설정.

Postgres·Redis·Qdrant·임베딩(Gemini) 설정은 history_shared.config.ServiceSettings 에서 물려받는다.
"""
from history_shared.config import ServiceSettings
from history_shared.events import STREAM_DOCUMENT_EVENTS


class Settings(ServiceSettings):
    server_port: int = 8083
    internal_api_key: str = ""

    # 객체 저장소(MinIO). 원본 파일을 읽기만 한다. 넣고 지우는 것은 document-service 의 몫이다
    storage_endpoint: str = "http://localhost:9000"
    storage_bucket: str = "documents"
    storage_region: str = "us-east-1"
    minio_root_user: str = "history"
    minio_root_password: str = ""

    # 문서 변경 이벤트 소비
    document_events_stream: str = STREAM_DOCUMENT_EVENTS
    consumer_group: str = "index-worker"
    consumer_name: str = ""  # 비우면 호스트 이름으로 만든다
    stream_claim_min_idle_ms: int = 60_000
    stream_max_deliveries: int = 5
    consumer_enabled: bool = True

    # 작업 러너
    runner_enabled: bool = True
    runner_interval_seconds: float = 1.0
    runner_max_per_tick: int = 20
    # RUNNING 으로 이 시간 넘게 남은 항목은 워커가 처리 도중 중단된 것으로 보고 되돌린다.
    # 속도 제한 대기를 포함한 가장 긴 색인 시간보다 넉넉해야 한다
    running_timeout_minutes: int = 30


settings = Settings()

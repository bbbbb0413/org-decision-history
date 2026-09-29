"""환경변수 설정. application.yml(chat-service)의 키와 1:1로 대응한다.

Postgres·Redis·Qdrant·임베딩(Gemini) 설정은 history_shared.config.ServiceSettings 에서 물려받는다.
임베딩 값은 index-worker 와 반드시 같아야 검색이 성립한다.
Postgres 스키마는 매 쿼리에서 명시적으로 붙인다(chat./indexing./document.) — JDBC 의 currentSchema 검색 경로에 기대지 않는다.
"""
from history_shared.config import ServiceSettings


class Settings(ServiceSettings):
    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    answer_jobs_topic: str = "chat.answer.jobs"
    answer_jobs_dlt_topic: str = "chat.answer.jobs.DLT"
    # rag-service 전용 그룹 이름을 쓴다. 할당 전략이 다른 클라이언트(Spring Kafka 의
    # RangeAssignor)가 같은 그룹을 쓴 적이 있으면 그 메타데이터가 __consumer_offsets 에
    # 남아, aiokafka(RoundRobinAssignor)의 join 이 InconsistentGroupProtocolError 로 거부된다.
    kafka_consumer_group: str = "rag-answer-workers"
    kafka_dlt_consumer_group: str = "rag-answer-workers-dlt"

    # 내부 서비스 인증
    internal_api_key: str = ""
    auth_service_url: str = "http://localhost:8081"

    # 리랭커 (Cross-Encoder 2차 재순위화)
    reranker_enabled: bool = True
    reranker_model: str = "ms-marco-MultiBERT-L-12"
    reranker_candidates_limit: int = 20
    reranker_top_k: int = 5
    reranker_min_score: float = 0.0

    # 생성(Groq) — 비어 있으면 근거 발췌 방식으로 답한다
    groq_api_key: str = ""
    groq_endpoint: str = "https://api.groq.com"
    primary_model: str = "openai/gpt-oss-120b"
    fallback_model: str = "openai/gpt-oss-20b"
    max_output_tokens: int = 1500

    # 실시간 웹훅 및 알림 (Notification Settings)
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

    # GraphRAG (Neo4j 지식 그래프 결합 검색)
    graphrag_enabled: bool = True
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"
    graphrag_max_hops: int = 2
    graphrag_max_subgraph_nodes: int = 25

    @property
    def has_groq_key(self) -> bool:
        return bool(self.groq_api_key and self.groq_api_key.strip())


settings = Settings()

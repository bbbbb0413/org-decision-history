import json

import structlog

from history_shared.config import ServiceSettings
from history_shared.logging import configure_logging, correlation_scope, new_correlation_id


def test_settings_read_environment_variables(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:word/1")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "5")
    settings = ServiceSettings()
    assert settings.postgres_dsn == "postgresql://history:p%40ss%3Aword%2F1@postgres:5432/knowledge"
    assert settings.qdrant_url == "http://qdrant:6333"
    assert settings.qdrant_collection == "document_chunks"
    assert settings.embedding_batch_size == 5
    assert settings.embedding_dimension == 768
    assert settings.has_gemini_key is False


def test_json_logs_carry_correlation_id_and_service(capsys):
    configure_logging("test-service")
    try:
        with correlation_scope("req_123"):
            structlog.get_logger().info("색인 완료", documentId=7)
        structlog.get_logger().info("범위 밖")
    finally:
        structlog.reset_defaults()

    inside, outside = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert inside["correlationId"] == "req_123"
    assert inside["service"] == "test-service"
    assert inside["documentId"] == 7
    assert inside["level"] == "info"
    assert "correlationId" not in outside


def test_new_correlation_id_format():
    value = new_correlation_id()
    assert value.startswith("req_") and len(value) == 16

"""구조화 로그와 상관관계 ID.

게이트웨이가 발급한 X-Correlation-Id 를 로그마다 correlationId 로 싣는다.
Java common 의 Correlation(MDC 키 correlationId)과 같은 이름을 써서 서비스 경계 너머로 한 요청을 추적한다.
"""
import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

CORRELATION_HEADER = "X-Correlation-Id"

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def new_correlation_id() -> str:
    """Java Correlation.newId() 와 같은 형식이다(req_ + 16진수 12자리)."""
    return "req_" + uuid.uuid4().hex[:12]


def current_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def correlation_scope(correlation_id: str | None) -> Iterator[None]:
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def add_correlation_id(logger, method_name, event_dict):
    correlation_id = _correlation_id.get()
    if correlation_id:
        event_dict["correlationId"] = correlation_id
    return event_dict


def configure_logging(service: str | None = None) -> None:
    """한 줄 JSON 로그. service 를 주면 모든 로그에 service 필드를 붙인다."""
    processors = [add_correlation_id]
    if service:

        def add_service(logger, method_name, event_dict):
            event_dict.setdefault("service", service)
            return event_dict

        processors.append(add_service)
    processors += [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(processors=processors)

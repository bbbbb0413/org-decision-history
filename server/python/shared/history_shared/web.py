"""FastAPI 공통 규약. Java common 의 서블릿 필터·ErrorResponse·PageResponse 와 같은 응답을 만든다.

`history-shared[web]` 로 설치한다(HTTP API 가 있는 워커만 필요하다).

    app = FastAPI()
    install_common(app, internal_api_key=settings.internal_api_key)
"""
import hmac
import math
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Generic, TypeVar

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from history_shared.logging import (
    CORRELATION_HEADER,
    correlation_scope,
    current_correlation_id,
    new_correlation_id,
)
from history_shared.metrics import METRICS_PATH, render as render_metrics, set_service_name

API_KEY_HEADER = "X-Internal-Key"
USER_ID_HEADER = "X-User-Id"
USER_ROLES_HEADER = "X-User-Roles"

# 내부 키 없이 통과시키는 경로. 오케스트레이터 헬스체크, 공개키 조회
OPEN_PREFIXES = ("/actuator", "/.well-known")

T = TypeVar("T")


class ErrorCode:
    """Java common ErrorCode 와 같은 값."""

    VALIDATION_FAILED = "VALIDATION_FAILED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_INVALID = "TOKEN_INVALID"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INTERNAL_AUTH_FAILED = "INTERNAL_AUTH_FAILED"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    NOT_FOUND = "NOT_FOUND"
    NOT_RETRYABLE = "NOT_RETRYABLE"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    DOWNLOAD_URL_EXPIRED = "DOWNLOAD_URL_EXPIRED"


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    """모든 실패 응답의 모양. null 필드는 응답에서 뺀다(Java @JsonInclude(NON_NULL))."""

    code: str
    message: str
    details: list[FieldError] | None = None
    queuePosition: int | None = None
    resumeAt: datetime | None = None
    correlationId: str | None = None

    def to_response(self, status_code: int) -> JSONResponse:
        return JSONResponse(status_code=status_code, content=self.model_dump(mode="json", exclude_none=True))


class ApiError(Exception):
    """핸들러에서 던지면 ErrorResponse 로 바뀐다."""

    def __init__(self, status_code: int, code: str, message: str, details: Sequence[FieldError] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = list(details) if details else None


class PageResponse(BaseModel, Generic[T]):
    content: list[T]
    page: int
    size: int
    totalElements: int
    totalPages: int

    @classmethod
    def of(cls, content: Sequence[T], *, page: int, size: int, total_elements: int) -> "PageResponse[T]":
        total_pages = 0 if size == 0 else math.ceil(total_elements / size)
        return cls(content=list(content), page=page, size=size, totalElements=total_elements, totalPages=total_pages)


class CorrelationIdMiddleware:
    """요청 헤더의 상관관계 ID 를 쓰고, 없으면 만든다. 로그 컨텍스트와 응답 헤더에 싣는다."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        provided = Headers(scope=scope).get(CORRELATION_HEADER)
        correlation_id = provided if provided and provided.strip() else new_correlation_id()

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[CORRELATION_HEADER] = correlation_id
            await send(message)

        with correlation_scope(correlation_id):
            await self.app(scope, receive, send_with_header)


class InternalKeyMiddleware:
    """게이트웨이를 거치지 않은 직접 호출을 막는다. 설정한 키가 비어 있으면 모두 거부한다."""

    def __init__(self, app: ASGIApp, *, expected_key: str, open_prefixes: Sequence[str] = OPEN_PREFIXES):
        self.app = app
        self._expected = (expected_key or "").encode()
        self._open_prefixes = tuple(open_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(self._open_prefixes):
            await self.app(scope, receive, send)
            return
        provided = (Headers(scope=scope).get(API_KEY_HEADER) or "").encode()
        if not self._expected or not hmac.compare_digest(provided, self._expected):
            response = JSONResponse(
                status_code=401,
                content={
                    "code": ErrorCode.INTERNAL_AUTH_FAILED,
                    "message": "내부 서비스 호출 검증에 실패했습니다.",
                    "correlationId": current_correlation_id() or "",
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


health_router = APIRouter()


@health_router.api_route("/actuator/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    """compose healthcheck·게이트웨이·auth-service Ops 가 Spring Actuator 와 같은 응답을 기대한다.

    Spring MVC는 GET 매핑에 HEAD도 자동으로 응답한다. `wget --spider`(compose 헬스체크)가 HEAD를
    보내므로 여기서도 두 메서드를 모두 받아야 405가 나지 않는다.
    """
    return {"status": "UP"}


@health_router.get(METRICS_PATH)
async def metrics() -> Response:
    """Spring actuator 와 같은 경로·형식으로 Prometheus 메트릭을 낸다.

    `/actuator` 는 OPEN_PREFIXES 라 내부 키 없이 열린다 — 헬스체크와 같은 취급이고,
    compose 에서 워커 포트를 공개하지 않으므로 도커 네트워크 안에서만 닿는다.
    """
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return ErrorResponse(
        code=exc.code, message=exc.message, details=exc.details, correlationId=current_correlation_id()
    ).to_response(exc.status_code)


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        FieldError(
            field=".".join(str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path", "header")),
            message=error.get("msg", ""),
        )
        for error in exc.errors()
    ]
    return ErrorResponse(
        code=ErrorCode.VALIDATION_FAILED,
        message="요청 값이 올바르지 않습니다.",
        details=details,
        correlationId=current_correlation_id(),
    ).to_response(400)


def install_common(app: FastAPI, *, internal_api_key: str, service: str | None = None) -> None:
    """공통 미들웨어·오류 응답·헬스체크·메트릭을 붙인다.

    service 를 주면 메트릭 덤프에 `app_info{service="..."}` 로 남는다.
    """
    # Starlette 는 나중에 추가한 미들웨어를 바깥에 둔다. 상관관계 ID 를 먼저 정해야 401 응답에도 실린다
    app.add_middleware(InternalKeyMiddleware, expected_key=internal_api_key)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.include_router(health_router)
    if service:
        set_service_name(service)


def format_instant(value: datetime | None) -> str | None:
    """Java Instant 의 JSON 표기(Jackson ISO_INSTANT)와 같은 UTC 문자열.

    소수 초는 0·3·6자리 중 필요한 만큼만 쓴다. 예: 2026-09-11T07:40:51.293230Z
    """
    if value is None:
        return None
    utc = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    micro = utc.microsecond
    if micro == 0:
        fraction = ""
    elif micro % 1000 == 0:
        fraction = f".{micro // 1000:03d}"
    else:
        fraction = f".{micro:06d}"
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + fraction + "Z"


def user_id(request: Request) -> int | None:
    value = request.headers.get(USER_ID_HEADER)
    return int(value) if value and value.strip().isdigit() else None


def user_roles(request: Request) -> frozenset[str]:
    """게이트웨이는 역할을 쉼표로 이어 보낸다."""
    value = request.headers.get(USER_ROLES_HEADER) or ""
    return frozenset(role.strip() for role in value.split(",") if role.strip())

"""색인 작업 HTTP API.

응답 필드와 시각 표기(ISO-8601 Instant)는 Spring 서비스와 같다.
- 시스템 관리자(X-User-Roles 에 SYSTEM_ADMIN)만 호출한다. 아니면 403 SCOPE_VIOLATION(auth-service 와 같은 코드).
- 재처리가 접수되면 그 항목을 돌려준다. 없는 항목이거나 다른 작업의 항목이면 404 다.
"""
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from history_shared.web import ApiError, ErrorCode, PageResponse, format_instant, install_common, user_id, user_roles
from pydantic import BaseModel

from app.documents import DocumentReader
from app.jobs import JobService
from app.queries import IndexQueries, ItemRow, JobRow
from app.settings import Settings

SYSTEM_ADMIN = "SYSTEM_ADMIN"


class JobView(BaseModel):
    """색인 작업."""

    jobKey: uuid.UUID
    triggerType: str
    status: str
    totalCount: int
    successCount: int
    failCount: int
    pendingCount: int
    progress: float
    requestedBy: int | None
    requestedAt: str
    finishedAt: str | None

    @classmethod
    def of(cls, row: JobRow) -> "JobView":
        return cls(
            jobKey=row.job_key,
            triggerType=row.trigger_type,
            status=row.status,
            totalCount=row.total_count,
            successCount=row.success_count,
            failCount=row.fail_count,
            pendingCount=row.pending_count,
            progress=row.progress,
            requestedBy=row.requested_by,
            requestedAt=format_instant(row.requested_at),
            finishedAt=format_instant(row.finished_at),
        )


class ItemView(BaseModel):
    """색인 작업 항목(문서 하나)."""

    id: int
    documentId: int
    documentTitle: str | None
    documentVersion: int
    status: str
    stage: str | None
    failureReason: str | None
    retryable: bool
    retryCount: int
    lastAttemptAt: str | None

    @classmethod
    def of(cls, row: ItemRow) -> "ItemView":
        return cls(
            id=row.id,
            documentId=row.document_id,
            documentTitle=row.document_title,
            documentVersion=row.document_version,
            status=row.status,
            stage=row.stage,
            failureReason=row.failure_reason,
            retryable=row.retryable,
            retryCount=row.retry_count,
            lastAttemptAt=format_instant(row.last_attempt_at),
        )


class BulkRequest(BaseModel):
    organizationId: int | None = None
    teamId: int | None = None


@dataclass
class ApiServices:
    jobs: JobService
    queries: IndexQueries
    documents: DocumentReader


def get_services(request: Request) -> ApiServices:
    return request.app.state.services


async def require_system_admin(request: Request) -> None:
    if SYSTEM_ADMIN not in user_roles(request):
        raise ApiError(403, ErrorCode.SCOPE_VIOLATION, "시스템 관리자만 색인 작업을 관리할 수 있습니다.")


Services = Annotated[ApiServices, Depends(get_services)]

router = APIRouter(prefix="/api/index-jobs", dependencies=[Depends(require_system_admin)])


@router.get("", response_model=PageResponse[JobView])
async def list_jobs(
    services: Services,
    page: Annotated[int, Query(ge=0)] = 0,
    size: Annotated[int, Query(ge=0, le=1000)] = 20,
):
    rows, total = await services.queries.list_jobs(page, size)
    return PageResponse.of([JobView.of(row) for row in rows], page=page, size=size, total_elements=total)


@router.post("", status_code=202, response_model=JobView)
async def create_bulk_job(request: Request, services: Services, body: BulkRequest | None = None):
    """전체 재색인용이다. 일반 최신화는 문서 변경 이벤트로 처리한다."""
    requester = user_id(request)
    if requester is None:
        raise ApiError(400, ErrorCode.VALIDATION_FAILED, "X-User-Id 헤더가 필요합니다.")
    targets = await services.documents.find_all_active(
        body.organizationId if body else None,
        body.teamId if body else None,
    )
    job_key = await services.jobs.accept_bulk(targets, requester)
    return JobView.of(await services.queries.get_job(job_key))


@router.get("/{job_key}", response_model=JobView)
async def get_job(job_key: uuid.UUID, services: Services):
    job = await services.queries.get_job(job_key)
    if job is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, "색인 작업을 찾을 수 없습니다.")
    return JobView.of(job)


@router.get("/{job_key}/items", response_model=list[ItemView])
async def list_items(job_key: uuid.UUID, services: Services, status: str | None = None):
    rows = await services.queries.list_items(job_key, status if status and status.strip() else None)
    return [ItemView.of(row) for row in rows]


@router.post("/{job_key}/items/{item_id}/retry", status_code=202, response_model=ItemView)
async def retry_item(job_key: uuid.UUID, item_id: int, services: Services):
    """자동 재시도 3회를 모두 소진한 항목에만 쓴다."""
    item = await services.queries.get_item(item_id)
    if item is None or item.job_key != job_key:
        raise ApiError(404, ErrorCode.NOT_FOUND, "색인 항목을 찾을 수 없습니다.")
    if not await services.jobs.retry_item(item_id):
        raise ApiError(
            409,
            ErrorCode.NOT_RETRYABLE,
            "재시도 대상이 아닙니다. 암호화·손상·미지원 형식은 다시 시도해도 결과가 같습니다.",
        )
    return ItemView.of(await services.queries.get_item(item_id))


def create_app(settings: Settings, *, lifespan=None) -> FastAPI:
    # 문서 화면은 내부 키 뒤에 있어 어차피 열 수 없다. 노출하지 않는다
    app = FastAPI(title="index-worker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    install_common(app, internal_api_key=settings.internal_api_key, service="index-worker")
    app.include_router(router)
    return app

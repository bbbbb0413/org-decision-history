"""색인 작업 상태 조회."""
import uuid
from dataclasses import dataclass
from datetime import datetime

import asyncpg

_JOB_COLUMNS = """
    j.job_key, j.trigger_type::text AS trigger_type, j.status::text AS status,
    j.total_count, j.success_count, j.fail_count, j.pending_count,
    j.requested_by, j.requested_at, j.finished_at
"""

_ITEM_SELECT = """
    SELECT i.id, i.document_id, d.title AS document_title, i.document_version,
           i.status::text AS status, i.stage::text AS stage, i.failure_reason,
           i.retryable, i.retry_count, i.last_attempt_at, j.job_key
    FROM indexing.index_job_item i
    JOIN indexing.index_job j ON j.id = i.index_job_id
    LEFT JOIN document.document d ON d.id = i.document_id
"""


@dataclass(frozen=True)
class JobRow:
    job_key: uuid.UUID
    trigger_type: str
    status: str
    total_count: int
    success_count: int
    fail_count: int
    pending_count: int
    requested_by: int | None
    requested_at: datetime
    finished_at: datetime | None

    @property
    def progress(self) -> float:
        """진행률은 저장하지 않고 계산한다. 따로 두면 집계와 어긋난다."""
        return 0.0 if self.total_count == 0 else (self.success_count + self.fail_count) / self.total_count


@dataclass(frozen=True)
class ItemRow:
    id: int
    document_id: int
    document_title: str | None
    document_version: int
    status: str
    stage: str | None
    failure_reason: str | None
    retryable: bool
    retry_count: int
    last_attempt_at: datetime | None
    job_key: uuid.UUID


class IndexQueries:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def list_jobs(self, page: int, size: int) -> tuple[list[JobRow], int]:
        rows = await self._pool.fetch(
            f"SELECT {_JOB_COLUMNS} FROM indexing.index_job j ORDER BY j.requested_at DESC LIMIT $1 OFFSET $2",
            size,
            page * size,
        )
        total = await self._pool.fetchval("SELECT count(*) FROM indexing.index_job")
        return [JobRow(**dict(row)) for row in rows], total

    async def get_job(self, job_key: uuid.UUID) -> JobRow | None:
        row = await self._pool.fetchrow(f"SELECT {_JOB_COLUMNS} FROM indexing.index_job j WHERE j.job_key = $1", job_key)
        return JobRow(**dict(row)) if row else None

    async def list_items(self, job_key: uuid.UUID, status: str | None) -> list[ItemRow]:
        """실패 목록을 볼 때는 status=FAILED 로 조회한다."""
        rows = await self._pool.fetch(
            _ITEM_SELECT + " WHERE j.job_key = $1 AND ($2::text IS NULL OR i.status::text = $2) ORDER BY i.id",
            job_key,
            status,
        )
        return [ItemRow(**dict(row)) for row in rows]

    async def get_item(self, item_id: int) -> ItemRow | None:
        row = await self._pool.fetchrow(_ITEM_SELECT + " WHERE i.id = $1", item_id)
        return ItemRow(**dict(row)) if row else None

"""색인 작업의 접수·선점·재시도·집계.

규약: contracts/indexing/README.md §12.2.
- 선점(RUNNING 기록)을 짧은 트랜잭션으로 커밋하고 파이프라인은 트랜잭션 밖에서 돌린다.
  속도 제한을 기다리는 동안 행 잠금과 DB 연결을 붙잡지 않고, 진행 중 상태가 관리자 화면에 보인다.
  대신 처리 도중 워커가 죽으면 RUNNING 으로 남으므로 recover_stale_running 이 일정 시간 뒤 되돌린다.
- 같은 이벤트가 동시에 접수돼 항목 삽입이 충돌하면 작업까지 되돌린다. 항목 없는 작업이 남지 않는다.
- 대상이 없는 일괄 색인은 바로 COMPLETED 로 닫는다. PENDING 으로 영원히 남지 않는다.
"""
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

import asyncpg
import structlog

from app.documents import DocumentSource
from app.pipeline import IndexingError, IndexStage

log = structlog.get_logger()

# 같은 간격으로 몰아치면 요청 한도에 걸린 상황을 더 악화시키므로 간격을 벌린다
BACKOFF = (timedelta(seconds=5), timedelta(minutes=1), timedelta(minutes=10))
MAX_RETRY = len(BACKOFF)
FAILURE_REASON_LIMIT = 300


class IndexTrigger(StrEnum):
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    DOCUMENT_UPDATE = "DOCUMENT_UPDATE"
    DOCUMENT_DELETE = "DOCUMENT_DELETE"
    BULK_INDEX = "BULK_INDEX"
    EXTERNAL_SYNC = "EXTERNAL_SYNC"


class Indexer(Protocol):
    async def index(self, document_id: int) -> int: ...


@dataclass(frozen=True)
class ClaimedItem:
    item_id: int
    job_id: int
    document_id: int
    retry_count: int


class _DuplicateEvent(Exception):
    """이미 접수한 이벤트. 트랜잭션을 되돌리는 데 쓴다."""


def truncate_reason(message: str | None) -> str | None:
    if message is None:
        return None
    return message if len(message) <= FAILURE_REASON_LIMIT else message[: FAILURE_REASON_LIMIT - 3] + "..."


_INSERT_JOB = """
    INSERT INTO indexing.index_job (job_key, trigger_type, status, total_count, pending_count, requested_by, finished_at)
    VALUES ($1, $2::indexing.index_trigger, $3::indexing.job_status, $4, $4, $5,
            CASE WHEN $3 = 'COMPLETED' THEN now() END)
    RETURNING id
"""

_INSERT_ITEM = """
    INSERT INTO indexing.index_job_item
        (index_job_id, document_id, document_version, status, event_key, next_attempt_at)
    VALUES ($1, $2, $3, 'PENDING', $4, now())
    ON CONFLICT (event_key) DO NOTHING
    RETURNING id
"""

# SKIP LOCKED: 워커를 여러 개 띄워도 같은 항목을 두 번 잡지 않는다
_CLAIM = """
    UPDATE indexing.index_job_item i
    SET status = 'RUNNING', last_attempt_at = now()
    WHERE i.id = (
        SELECT id FROM indexing.index_job_item
        WHERE status = 'PENDING'
          AND retryable = TRUE
          AND (next_attempt_at IS NULL OR next_attempt_at <= now())
        ORDER BY id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING i.id, i.index_job_id, i.document_id, i.retry_count
"""

# 집계는 항목 상태에서 다시 계산한다. 따로 세어 두면 어긋난다
_REFRESH_JOB = """
    UPDATE indexing.index_job j
    SET success_count = c.success,
        fail_count    = c.fail,
        pending_count = c.pending,
        status = CASE WHEN c.pending = 0 AND c.fail > 0 THEN 'FAILED'
                      WHEN c.pending = 0 THEN 'COMPLETED'
                      ELSE 'RUNNING' END::indexing.job_status,
        finished_at = CASE WHEN c.pending = 0 THEN now() ELSE NULL END
    FROM (
        SELECT index_job_id,
               count(*) FILTER (WHERE status = 'COMPLETED') AS success,
               count(*) FILTER (WHERE status = 'FAILED')    AS fail,
               count(*) FILTER (WHERE status IN ('PENDING', 'RUNNING')) AS pending
        FROM indexing.index_job_item
        WHERE index_job_id = $1
        GROUP BY index_job_id
    ) c
    WHERE j.id = c.index_job_id
"""

_MARK_FAILED = """
    UPDATE indexing.index_job_item
    SET status = $2::indexing.job_status,
        stage = $3::indexing.index_stage,
        failure_reason = $4,
        retryable = $5,
        retry_count = $6,
        next_attempt_at = now() + $7::interval
    WHERE id = $1
"""

_RECOVER_STALE = """
    UPDATE indexing.index_job_item
    SET status = CASE WHEN retry_count < $1 THEN 'PENDING' ELSE 'FAILED' END::indexing.job_status,
        failure_reason = CASE WHEN retry_count < $1 THEN '처리 중 워커가 중단되어 다시 시도합니다'
                              ELSE '처리 중 워커가 중단되었고 재시도 한도를 넘었습니다' END,
        next_attempt_at = CASE WHEN retry_count < $1 THEN now() END,
        stage = NULL,
        retry_count = retry_count + 1
    WHERE status = 'RUNNING' AND last_attempt_at < now() - $2::interval
    RETURNING id, index_job_id
"""


class JobService:
    def __init__(self, pool: asyncpg.Pool, indexer: Indexer | None, *, running_timeout: timedelta = timedelta(minutes=30)):
        self._pool = pool
        self._indexer = indexer
        self._running_timeout = running_timeout

    async def accept(
        self,
        document_id: int,
        version: int,
        trigger: IndexTrigger,
        event_key: str,
        requested_by: int | None = None,
    ) -> uuid.UUID | None:
        """색인 요청을 접수한다. 이미 접수한 이벤트면 None."""
        job_key = uuid.uuid4()
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                if await conn.fetchval("SELECT 1 FROM indexing.index_job_item WHERE event_key = $1", event_key):
                    raise _DuplicateEvent
                job_id = await conn.fetchval(_INSERT_JOB, job_key, str(trigger), "PENDING", 1, requested_by)
                if await conn.fetchval(_INSERT_ITEM, job_id, document_id, version, event_key) is None:
                    raise _DuplicateEvent  # 동시에 들어온 같은 이벤트. 작업까지 되돌린다
        except _DuplicateEvent:
            log.debug("이미 접수한 이벤트라 건너뛴다", eventKey=event_key)
            return None
        return job_key

    async def accept_bulk(self, targets: Sequence[DocumentSource], requested_by: int | None) -> uuid.UUID:
        """일괄 색인. 같은 문서를 다시 넣을 수 있어야 하므로 멱등 키에 jobKey 를 넣는다."""
        job_key = uuid.uuid4()
        status = "PENDING" if targets else "COMPLETED"
        async with self._pool.acquire() as conn, conn.transaction():
            job_id = await conn.fetchval(
                _INSERT_JOB, job_key, str(IndexTrigger.BULK_INDEX), status, len(targets), requested_by
            )
            await conn.executemany(
                _INSERT_ITEM,
                [(job_id, t.id, t.version, f"bulk:{job_key}:{t.id}:{t.version}") for t in targets],
            )
        return job_key

    async def claim_next(self) -> ClaimedItem | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(_CLAIM)
            if row is None:
                return None
            await conn.execute(_REFRESH_JOB, row["index_job_id"])
        return ClaimedItem(row["id"], row["index_job_id"], row["document_id"], row["retry_count"])

    async def process_next(self) -> bool:
        """대기 항목 하나를 선점해 색인한다. 처리할 항목이 없으면 False."""
        item = await self.claim_next()
        if item is None:
            return False
        try:
            await self._indexer.index(item.document_id)
        except IndexingError as e:
            await self._mark_failed(item, e)
        except Exception as e:  # noqa: BLE001 — 예상하지 못한 오류는 다시 시도해 본다
            await self._mark_failed(item, IndexingError(IndexStage.PARSE, str(e) or type(e).__name__, retryable=True))
        else:
            await self._mark_succeeded(item)
        return True

    async def _mark_succeeded(self, item: ClaimedItem) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE indexing.index_job_item SET status = 'COMPLETED', stage = NULL, failure_reason = NULL WHERE id = $1",
                item.item_id,
            )
            await conn.execute(_REFRESH_JOB, item.job_id)

    async def _mark_failed(self, item: ClaimedItem, error: IndexingError) -> None:
        """재시도 불가는 바로 FAILED, 자동 재시도는 간격을 벌려 PENDING, 3회를 넘기면 FAILED(수동 처리)."""
        next_retry = item.retry_count + 1
        can_retry = error.retryable and next_retry <= MAX_RETRY
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                _MARK_FAILED,
                item.item_id,
                "PENDING" if can_retry else "FAILED",
                str(error.stage),
                truncate_reason(str(error)),
                error.retryable,
                next_retry,
                BACKOFF[next_retry - 1] if can_retry else None,
            )
            await conn.execute(_REFRESH_JOB, item.job_id)
        log.warning(
            "색인 실패",
            itemId=item.item_id,
            documentId=item.document_id,
            stage=str(error.stage),
            retryable=error.retryable,
            attempt=next_retry,
            reason=str(error),
        )

    async def retry_item(self, item_id: int) -> bool:
        """관리자의 수동 재처리. 재시도 대상이 아닌 항목은 받지 않는다. 다시 해도 결과가 같다."""
        status = await self._pool.execute(
            """
            UPDATE indexing.index_job_item
            SET status = 'PENDING', retry_count = 0, next_attempt_at = now(), failure_reason = NULL, stage = NULL
            WHERE id = $1 AND status = 'FAILED' AND retryable = TRUE
            """,
            item_id,
        )
        updated = int(status.split()[-1]) > 0
        if updated:
            job_id = await self._pool.fetchval("SELECT index_job_id FROM indexing.index_job_item WHERE id = $1", item_id)
            await self._pool.execute(_REFRESH_JOB, job_id)
        return updated

    async def recover_stale_running(self) -> int:
        """처리 도중 워커가 멈춰 RUNNING 으로 남은 항목을 되돌린다. 한 번의 시도로 센다."""
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(_RECOVER_STALE, MAX_RETRY, self._running_timeout)
            for job_id in {row["index_job_id"] for row in rows}:
                await conn.execute(_REFRESH_JOB, job_id)
        if rows:
            log.warning("멈춘 색인 항목을 되돌렸다", items=[row["id"] for row in rows])
        return len(rows)

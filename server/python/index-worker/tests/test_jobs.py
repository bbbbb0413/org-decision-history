"""색인 작업 접수·선점·재시도·집계. 실제 PostgreSQL 에서 돌린다."""
import asyncio
import uuid
from datetime import timedelta

import pytest
from fakes import FakeIndexer, source

from app.jobs import FAILURE_REASON_LIMIT, IndexTrigger, JobService, truncate_reason
from app.pipeline import IndexingError, IndexStage


@pytest.fixture
def indexer():
    return FakeIndexer()


@pytest.fixture
def jobs(pool, indexer):
    return JobService(pool, indexer)


async def job_of(pool, job_key: uuid.UUID) -> dict:
    return dict(await pool.fetchrow("SELECT *, status::text AS status FROM indexing.index_job WHERE job_key = $1", job_key))


async def items_of(pool, job_key: uuid.UUID) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT i.*, i.status::text AS status, i.stage::text AS stage,
               extract(epoch FROM i.next_attempt_at - now()) AS wait_seconds
        FROM indexing.index_job_item i JOIN indexing.index_job j ON j.id = i.index_job_id
        WHERE j.job_key = $1 ORDER BY i.id
        """,
        job_key,
    )
    return [dict(row) for row in rows]


async def make_due(pool) -> None:
    await pool.execute("UPDATE indexing.index_job_item SET next_attempt_at = now() WHERE status = 'PENDING'")


async def test_accept_is_idempotent_by_event_key(pool, jobs):
    first = await jobs.accept(1, 2, IndexTrigger.DOCUMENT_UPDATE, "evt:1:2:DOCUMENT_UPDATED", requested_by=9)
    second = await jobs.accept(1, 2, IndexTrigger.DOCUMENT_UPDATE, "evt:1:2:DOCUMENT_UPDATED")

    assert first is not None and second is None
    job = await job_of(pool, first)
    assert (job["trigger_type"], job["status"], job["total_count"], job["pending_count"], job["requested_by"]) == (
        "DOCUMENT_UPDATE",
        "PENDING",
        1,
        1,
        9,
    )
    [item] = await items_of(pool, first)
    assert (item["document_id"], item["document_version"], item["status"]) == (1, 2, "PENDING")
    assert await pool.fetchval("SELECT count(*) FROM indexing.index_job") == 1


async def test_concurrent_duplicate_events_leave_one_job(pool, jobs):
    results = await asyncio.gather(
        *(jobs.accept(1, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:1:1:DOCUMENT_CREATED") for _ in range(6))
    )

    assert sum(result is not None for result in results) == 1
    # 항목 삽입이 충돌하면 작업 행도 남지 않는다
    assert await pool.fetchval("SELECT count(*) FROM indexing.index_job") == 1
    assert await pool.fetchval("SELECT count(*) FROM indexing.index_job_item") == 1


async def test_bulk_job_creates_item_per_target(pool, jobs):
    job_key = await jobs.accept_bulk([source(10), source(11, version=3)], requested_by=7)

    job = await job_of(pool, job_key)
    assert (job["trigger_type"], job["status"], job["total_count"], job["pending_count"]) == ("BULK_INDEX", "PENDING", 2, 2)
    assert [(i["document_id"], i["document_version"], i["event_key"]) for i in await items_of(pool, job_key)] == [
        (10, 1, f"bulk:{job_key}:10:1"),
        (11, 3, f"bulk:{job_key}:11:3"),
    ]
    # 같은 문서를 다시 일괄 색인할 수 있다
    assert await jobs.accept_bulk([source(10)], requested_by=7) != job_key


async def test_empty_bulk_job_is_completed_immediately(pool, jobs):
    job = await job_of(pool, await jobs.accept_bulk([], requested_by=7))

    assert (job["status"], job["total_count"], job["pending_count"]) == ("COMPLETED", 0, 0)
    assert job["finished_at"] is not None


async def test_concurrent_claims_never_take_the_same_item(pool, jobs):
    await jobs.accept_bulk([source(i) for i in range(1, 6)], requested_by=None)

    claimed = await asyncio.gather(*(jobs.claim_next() for _ in range(5)))

    assert all(claimed)
    assert len({item.item_id for item in claimed}) == 5
    assert await jobs.claim_next() is None


async def test_claim_marks_item_and_job_running(pool, jobs):
    job_key = await jobs.accept(5, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:5:1:DOCUMENT_CREATED")

    claimed = await jobs.claim_next()

    assert claimed.document_id == 5 and claimed.retry_count == 0
    [item] = await items_of(pool, job_key)
    assert item["status"] == "RUNNING" and item["last_attempt_at"] is not None
    # 파이프라인이 도는 동안 관리자 화면에 진행 중으로 보인다
    assert (await job_of(pool, job_key))["status"] == "RUNNING"


async def test_success_completes_job(pool, jobs, indexer):
    job_key = await jobs.accept(5, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:5:1:DOCUMENT_CREATED")

    assert await jobs.process_next()
    assert not await jobs.process_next()

    assert indexer.calls == [5]
    [item] = await items_of(pool, job_key)
    assert (item["status"], item["stage"], item["failure_reason"]) == ("COMPLETED", None, None)
    job = await job_of(pool, job_key)
    assert (job["status"], job["success_count"], job["fail_count"], job["pending_count"]) == ("COMPLETED", 1, 0, 0)
    assert job["finished_at"] is not None


async def test_retryable_failure_backs_off_then_needs_manual_retry(pool, jobs, indexer):
    indexer.errors[7] = IndexingError(IndexStage.EMBED, "임베딩 요청 한도 초과(429)", retryable=True)
    job_key = await jobs.accept(7, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:7:1:DOCUMENT_CREATED")

    for attempt, backoff in enumerate((5, 60, 600), start=1):
        assert await jobs.process_next()
        [item] = await items_of(pool, job_key)
        assert (item["status"], item["stage"], item["retry_count"], item["retryable"]) == ("PENDING", "EMBED", attempt, True)
        assert item["failure_reason"] == "임베딩 요청 한도 초과(429)"
        assert backoff - 5 < item["wait_seconds"] <= backoff
        assert (await job_of(pool, job_key))["status"] == "RUNNING"
        # 간격이 지나기 전에는 다시 잡지 않는다
        assert await jobs.claim_next() is None
        await make_due(pool)

    assert await jobs.process_next()
    [item] = await items_of(pool, job_key)
    assert (item["status"], item["retry_count"], item["retryable"], item["next_attempt_at"]) == ("FAILED", 4, True, None)
    job = await job_of(pool, job_key)
    assert (job["status"], job["fail_count"], job["pending_count"]) == ("FAILED", 1, 0)

    # 관리자가 다시 처리한다
    indexer.errors.clear()
    assert await jobs.retry_item(item["id"])
    [item] = await items_of(pool, job_key)
    assert (item["status"], item["retry_count"], item["stage"], item["failure_reason"]) == ("PENDING", 0, None, None)
    job = await job_of(pool, job_key)
    assert (job["status"], job["fail_count"], job["pending_count"], job["finished_at"]) == ("RUNNING", 0, 1, None)
    assert not await jobs.retry_item(item["id"]), "대기 중인 항목은 다시 접수하지 않는다"

    assert await jobs.process_next()
    assert (await job_of(pool, job_key))["status"] == "COMPLETED"


async def test_non_retryable_failure_fails_immediately(pool, jobs, indexer):
    indexer.errors[8] = IndexingError(IndexStage.PARSE, "암호화된 파일이라 본문을 추출할 수 없습니다", retryable=False)
    job_key = await jobs.accept(8, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:8:1:DOCUMENT_CREATED")

    assert await jobs.process_next()

    [item] = await items_of(pool, job_key)
    assert (item["status"], item["stage"], item["retryable"], item["retry_count"]) == ("FAILED", "PARSE", False, 1)
    assert not await jobs.retry_item(item["id"])


async def test_unexpected_error_is_retried_as_parse_failure(pool, jobs, indexer):
    indexer.errors[9] = RuntimeError()
    job_key = await jobs.accept(9, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:9:1:DOCUMENT_CREATED")

    assert await jobs.process_next()

    [item] = await items_of(pool, job_key)
    assert (item["status"], item["stage"], item["retryable"]) == ("PENDING", "PARSE", True)
    # 메시지 없는 예외면 예외 이름이라도 남긴다
    assert item["failure_reason"] == "RuntimeError"


async def test_long_failure_reason_is_truncated(pool, jobs, indexer):
    indexer.errors[3] = IndexingError(IndexStage.EMBED, "가" * 1000, retryable=True)
    job_key = await jobs.accept(3, 1, IndexTrigger.DOCUMENT_UPLOAD, "evt:3:1:DOCUMENT_CREATED")

    assert await jobs.process_next()

    [item] = await items_of(pool, job_key)
    assert len(item["failure_reason"]) == FAILURE_REASON_LIMIT and item["failure_reason"].endswith("...")
    assert truncate_reason(None) is None
    assert truncate_reason("가" * FAILURE_REASON_LIMIT) == "가" * FAILURE_REASON_LIMIT


async def test_mixed_results_fail_job_with_counts(pool, jobs, indexer):
    indexer.errors[2] = IndexingError(IndexStage.CHUNK, "본문이 비어 있어 색인할 내용이 없습니다", retryable=False)
    job_key = await jobs.accept_bulk([source(1), source(2)], requested_by=7)

    assert await jobs.process_next() and await jobs.process_next()

    job = await job_of(pool, job_key)
    assert (job["status"], job["success_count"], job["fail_count"], job["pending_count"]) == ("FAILED", 1, 1, 0)


async def test_stale_running_items_are_recovered(pool, jobs):
    job_key = await jobs.accept_bulk([source(1), source(2)], requested_by=None)
    first, second = await jobs.claim_next(), await jobs.claim_next()
    assert await jobs.recover_stale_running() == 0, "생존 신호가 살아 있는 RUNNING 은 처리 중이다"

    stale = "UPDATE indexing.index_job_item SET heartbeat_at = now() - interval '61 seconds', retry_count = $2 WHERE id = $1"
    await pool.execute(stale, first.item_id, 0)
    await pool.execute(stale, second.item_id, 3)

    assert await jobs.recover_stale_running() == 2

    recovered, exhausted = await items_of(pool, job_key)
    assert (recovered["status"], recovered["retry_count"], recovered["stage"]) == ("PENDING", 1, None)
    assert recovered["failure_reason"] == "처리 중 워커가 중단되어 다시 시도합니다"
    assert (exhausted["status"], exhausted["retry_count"], exhausted["retryable"]) == ("FAILED", 4, True)
    job = await job_of(pool, job_key)
    assert (job["status"], job["fail_count"], job["pending_count"]) == ("RUNNING", 1, 1)
    assert (await jobs.claim_next()).item_id == first.item_id


class BlockingIndexer:
    """release 가 설정될 때까지 색인을 끝내지 않는다. 오래 걸리는 색인(속도 제한 대기 등)을 흉내 낸다."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def index(self, document_id: int) -> int:
        self.started.set()
        await self.release.wait()
        return 1


async def test_long_running_item_is_kept_alive_by_heartbeat(pool):
    indexer = BlockingIndexer()
    jobs = JobService(pool, indexer, heartbeat_interval=timedelta(seconds=0.1), stale_after=timedelta(seconds=0.5))
    job_key = await jobs.accept_bulk([source(1)], requested_by=None)

    processing = asyncio.create_task(jobs.process_next())
    await indexer.started.wait()
    for _ in range(3):  # 회수 기준(0.5초)의 세 배 동안 계속 확인한다
        await asyncio.sleep(0.5)
        assert await jobs.recover_stale_running() == 0
    indexer.release.set()
    assert await processing

    [item] = await items_of(pool, job_key)
    assert (item["status"], item["retry_count"]) == ("COMPLETED", 0)


async def test_cancelled_processing_releases_item_without_counting_a_retry(pool):
    indexer = BlockingIndexer()
    jobs = JobService(pool, indexer)
    job_key = await jobs.accept_bulk([source(1)], requested_by=None)

    processing = asyncio.create_task(jobs.process_next())
    await indexer.started.wait()
    processing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await processing

    [item] = await items_of(pool, job_key)
    assert (item["status"], item["retry_count"], item["heartbeat_at"]) == ("PENDING", 0, None)
    assert (await jobs.claim_next()).item_id == item["id"], "다음 워커가 바로 이어받는다"


def test_stale_after_must_leave_room_for_missed_heartbeats():
    with pytest.raises(ValueError):
        JobService(None, None, heartbeat_interval=timedelta(seconds=10), stale_after=timedelta(seconds=20))

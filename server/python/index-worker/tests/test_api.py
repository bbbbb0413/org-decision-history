"""색인 작업 HTTP API. 응답 모양은 프론트 관리자 화면이 그대로 쓰는 계약이다."""
import re
import uuid

import httpx
import pytest
from fakes import FakeIndexer

from app.api import ApiServices, create_app
from app.documents import DocumentReader
from app.jobs import JobService
from app.pipeline import IndexingError, IndexStage
from app.queries import IndexQueries
from app.settings import Settings

KEY = "internal-secret"
ADMIN = {"X-Internal-Key": KEY, "X-User-Id": "7", "X-User-Roles": "USER,SYSTEM_ADMIN"}
# IndexDtos.Job · IndexDtos.JobItem 필드 순서
JOB_FIELDS = [
    "jobKey", "triggerType", "status", "totalCount", "successCount", "failCount", "pendingCount",
    "progress", "requestedBy", "requestedAt", "finishedAt",
]
ITEM_FIELDS = [
    "id", "documentId", "documentTitle", "documentVersion", "status", "stage", "failureReason",
    "retryable", "retryCount", "lastAttemptAt",
]
INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3}|\.\d{6})?Z$")


@pytest.fixture
def indexer():
    return FakeIndexer()


@pytest.fixture
def jobs(pool, indexer):
    return JobService(pool, indexer)


@pytest.fixture
async def client(pool, jobs):
    app = create_app(Settings(internal_api_key=KEY))
    app.state.services = ApiServices(jobs=jobs, queries=IndexQueries(pool), documents=DocumentReader(pool))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://index-worker") as c:
        yield c


async def test_requires_internal_key_and_system_admin(client):
    unauthenticated = await client.get("/api/index-jobs")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "INTERNAL_AUTH_FAILED"

    # 내부 키가 있어도 시스템 관리자가 아니면 막는다
    for roles in ("ORG_ADMIN", None):
        headers = {"X-Internal-Key": KEY, "X-User-Id": "7"} | ({"X-User-Roles": roles} if roles else {})
        forbidden = await client.get("/api/index-jobs", headers=headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "SCOPE_VIOLATION"

    health = await client.get("/actuator/health")
    assert (health.status_code, health.json()) == (200, {"status": "UP"})


async def test_create_bulk_job_targets_active_documents_in_scope(client, add_document):
    await add_document(organization_id=1, team_id=3)
    await add_document(organization_id=1)
    await add_document(organization_id=1, deleted=True)
    await add_document(organization_id=2)

    response = await client.post("/api/index-jobs", json={"organizationId": 1}, headers=ADMIN)

    assert response.status_code == 202
    body = response.json()
    assert list(body) == JOB_FIELDS
    assert uuid.UUID(body["jobKey"])
    assert {k: body[k] for k in JOB_FIELDS if k not in ("jobKey", "requestedAt")} == {
        "triggerType": "BULK_INDEX",
        "status": "PENDING",
        "totalCount": 2,
        "successCount": 0,
        "failCount": 0,
        "pendingCount": 2,
        "progress": 0.0,
        "requestedBy": 7,
        "finishedAt": None,
    }
    assert INSTANT.match(body["requestedAt"])

    team_only = await client.post("/api/index-jobs", json={"teamId": 3}, headers=ADMIN)
    everything = await client.post("/api/index-jobs", headers=ADMIN)
    assert team_only.json()["totalCount"] == 1
    assert everything.json()["totalCount"] == 3


async def test_create_bulk_job_requires_user_id(client):
    response = await client.post("/api/index-jobs", headers={k: v for k, v in ADMIN.items() if k != "X-User-Id"})

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"


async def test_empty_bulk_job_is_completed(client):
    body = (await client.post("/api/index-jobs", json={"organizationId": 99}, headers=ADMIN)).json()

    assert (body["status"], body["totalCount"], body["progress"]) == ("COMPLETED", 0, 0.0)
    assert INSTANT.match(body["finishedAt"])


async def test_list_and_get_jobs(client, add_document):
    await add_document()
    first = (await client.post("/api/index-jobs", headers=ADMIN)).json()
    second = (await client.post("/api/index-jobs", headers=ADMIN)).json()

    page = (await client.get("/api/index-jobs", params={"page": 0, "size": 1}, headers=ADMIN)).json()
    assert page["totalElements"] == 2
    assert [job["jobKey"] for job in page["content"]] == [second["jobKey"]], "최근 요청이 먼저다"

    found = await client.get(f"/api/index-jobs/{first['jobKey']}", headers=ADMIN)
    assert (found.status_code, found.json()) == (200, first)

    missing = await client.get(f"/api/index-jobs/{uuid.uuid4()}", headers=ADMIN)
    assert (missing.status_code, missing.json()["code"]) == (404, "NOT_FOUND")
    malformed = await client.get("/api/index-jobs/not-a-uuid", headers=ADMIN)
    assert (malformed.status_code, malformed.json()["code"]) == (400, "VALIDATION_FAILED")


async def test_items_progress_and_retry(client, add_document, jobs, indexer, pool):
    ok = await add_document(title="성공 문서")
    broken = await add_document(title="암호화 문서", version=2)
    flaky = await add_document(title="한도 초과 문서")
    indexer.errors[broken] = IndexingError(IndexStage.PARSE, "암호화된 파일이라 본문을 추출할 수 없습니다", retryable=False)
    indexer.errors[flaky] = IndexingError(IndexStage.EMBED, "요청 한도 초과", retryable=True)
    job_key = (await client.post("/api/index-jobs", headers=ADMIN)).json()["jobKey"]
    while await jobs.process_next():
        pass
    # 자동 재시도를 모두 소진한 상태로 만든다
    await pool.execute(
        "UPDATE indexing.index_job_item SET status = 'FAILED', retry_count = 4 WHERE document_id = $1", flaky
    )
    await jobs.retry_item(-1)  # 없는 항목은 아무것도 바꾸지 않는다

    items = (await client.get(f"/api/index-jobs/{job_key}/items", headers=ADMIN)).json()
    assert [list(item) for item in items] == [ITEM_FIELDS] * 3
    ok_item, broken_item, flaky_item = items
    assert {k: ok_item[k] for k in ITEM_FIELDS if k not in ("id", "lastAttemptAt")} == {
        "documentId": ok,
        "documentTitle": "성공 문서",
        "documentVersion": 1,
        "status": "COMPLETED",
        "stage": None,
        "failureReason": None,
        "retryable": True,
        "retryCount": 0,
    }
    assert INSTANT.match(ok_item["lastAttemptAt"])
    assert (broken_item["documentVersion"], broken_item["stage"], broken_item["retryable"]) == (2, "PARSE", False)

    failed = (await client.get(f"/api/index-jobs/{job_key}/items", params={"status": "FAILED"}, headers=ADMIN)).json()
    assert [item["documentId"] for item in failed] == [broken, flaky]
    blank = (await client.get(f"/api/index-jobs/{job_key}/items", params={"status": " "}, headers=ADMIN)).json()
    assert len(blank) == 3

    not_retryable = await client.post(f"/api/index-jobs/{job_key}/items/{broken_item['id']}/retry", headers=ADMIN)
    assert (not_retryable.status_code, not_retryable.json()["code"]) == (409, "NOT_RETRYABLE")

    # 재처리를 접수하면 그 항목을 돌려준다
    retried = await client.post(f"/api/index-jobs/{job_key}/items/{flaky_item['id']}/retry", headers=ADMIN)
    assert retried.status_code == 202
    assert {k: retried.json()[k] for k in ("id", "status", "retryCount", "stage", "failureReason")} == {
        "id": flaky_item["id"],
        "status": "PENDING",
        "retryCount": 0,
        "stage": None,
        "failureReason": None,
    }

    job = (await client.get(f"/api/index-jobs/{job_key}", headers=ADMIN)).json()
    assert (job["status"], job["successCount"], job["failCount"], job["pendingCount"]) == ("RUNNING", 1, 1, 1)
    assert job["progress"] == pytest.approx(2 / 3)

    other_job = (await client.post("/api/index-jobs", headers=ADMIN)).json()["jobKey"]
    wrong_job = await client.post(f"/api/index-jobs/{other_job}/items/{flaky_item['id']}/retry", headers=ADMIN)
    unknown = await client.post(f"/api/index-jobs/{job_key}/items/999999/retry", headers=ADMIN)
    assert (wrong_job.status_code, unknown.status_code) == (404, 404)

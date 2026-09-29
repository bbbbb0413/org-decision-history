"""문서 변경 이벤트를 색인 요청으로 바꾸는 소비자."""
import json
import uuid

import pytest
from history_shared.streams import PermanentMessageError, StreamMessage

from app.consumer import DocumentEventHandler, build_consumer, event_key, trigger_for
from app.jobs import IndexTrigger, JobService
from app.settings import Settings


def fields(event_type: str = "DOCUMENT_UPDATED", **payload) -> dict[str, str]:
    """document-service 가 발행하는 모양(contracts/events/document-events.schema.json)."""
    body = {"documentId": 12, "version": 3, "organizationId": 1, "teamId": "", "documentType": "MEETING_NOTE"} | payload
    return {"aggregateId": str(body["documentId"]), "eventType": event_type, "payload": json.dumps(body)}


class RecordingJobs:
    def __init__(self):
        self.accepted: list[tuple] = []

    async def accept(self, document_id, version, trigger, key, requested_by=None):
        self.accepted.append((document_id, version, trigger, key))
        return uuid.uuid4()


def test_event_type_maps_to_trigger():
    assert trigger_for("DOCUMENT_CREATED") == IndexTrigger.DOCUMENT_UPLOAD
    assert trigger_for("DOCUMENT_UPDATED") == IndexTrigger.DOCUMENT_UPDATE
    assert trigger_for("DOCUMENT_DELETED") == IndexTrigger.DOCUMENT_DELETE
    # 권한 변경은 payload 의 scope_key 를 새로 새겨야 하므로 재색인한다
    assert trigger_for("PERMISSION_CHANGED") == IndexTrigger.DOCUMENT_UPDATE


async def test_handler_accepts_event_with_idempotency_key():
    jobs = RecordingJobs()

    await DocumentEventHandler(jobs)(StreamMessage("1-0", fields("DOCUMENT_DELETED", teamId=4)))

    assert jobs.accepted == [(12, 3, IndexTrigger.DOCUMENT_DELETE, "evt:12:3:DOCUMENT_DELETED")]


@pytest.mark.parametrize(
    "bad",
    [
        {"eventType": "DOCUMENT_UPDATED", "payload": "{"},
        {"eventType": "DOCUMENT_ARCHIVED", "payload": "{}"},
        fields(documentId="열둘"),
    ],
)
async def test_contract_violation_is_permanent(bad):
    jobs = RecordingJobs()

    with pytest.raises(PermanentMessageError):
        await DocumentEventHandler(jobs)(StreamMessage("1-0", bad))
    assert jobs.accepted == []


async def test_consumer_accepts_duplicate_events_once_and_dead_letters_bad_ones(pool, redis_client):
    stream = f"test:document-events:{uuid.uuid4().hex}"
    settings = Settings(document_events_stream=stream, consumer_name="index-worker-test")
    consumer = build_consumer(redis_client, JobService(pool, None), settings)
    try:
        await consumer.ensure_group()
        # 발행기가 같은 이벤트를 두 번 보낸 경우(발행 후 published_at 기록 전에 죽음)
        await redis_client.xadd(stream, fields("DOCUMENT_CREATED"))
        await redis_client.xadd(stream, fields("DOCUMENT_CREATED"))
        await redis_client.xadd(stream, {"eventType": "DOCUMENT_UPDATED", "payload": "{"})

        await consumer.poll()

        rows = await pool.fetch("SELECT document_id, document_version, event_key FROM indexing.index_job_item")
        assert [tuple(row) for row in rows] == [(12, 3, event_key_of_created())]
        assert (await redis_client.xpending(stream, "index-worker"))["pending"] == 0
        [(_, dead)] = await redis_client.xrange(consumer.dlq_stream)
        assert (dead["dlq.group"], dead["eventType"]) == ("index-worker", "DOCUMENT_UPDATED")
    finally:
        await redis_client.delete(stream, consumer.dlq_stream)


def event_key_of_created() -> str:
    from history_shared.events import DocumentEvent

    return event_key(DocumentEvent.from_fields(fields("DOCUMENT_CREATED")))


def fields_with_event_id(event_id: str, **payload) -> dict[str, str]:
    base = fields(**payload)
    return base | {"eventId": event_id}


def test_event_key_includes_outbox_row_id():
    """같은 판에 원본 파일을 다시 올리면 (문서, 판, 종류)가 똑같아 접수 키가 겹친다.

    `index_job_item.event_key` 가 UNIQUE 이고 접수는 `ON CONFLICT DO NOTHING` 이라,
    키가 겹치면 두 번째 재색인이 **조용히 건너뛰어진다** — 화면에는 새 파일이 보이는데
    검색·인용은 이전 내용을 가리키게 된다. 아웃박스 행 번호를 키에 넣어 그걸 막는다.
    """
    first = event_key(_parse(fields_with_event_id("101")))
    second = event_key(_parse(fields_with_event_id("102")))

    assert first != second
    assert first.endswith(":101")


def test_event_key_is_stable_for_redelivery_of_same_row():
    # 최소 한 번 전달이므로 같은 아웃박스 행이 두 번 올 수 있다. 그때는 키가 같아야 한다.
    key = event_key(_parse(fields_with_event_id("101")))

    assert event_key(_parse(fields_with_event_id("101"))) == key


def test_event_key_falls_back_when_event_id_absent():
    # eventId 를 넣기 전에 발행된 항목에는 그 필드가 없다. 그때도 접수는 되어야 한다.
    assert event_key(_parse(fields())) == "evt:12:3:DOCUMENT_UPDATED"


def test_event_key_falls_back_when_event_id_unparsable():
    # 값이 깨졌다고 이벤트를 DLQ 로 보내지 않는다 — 이벤트 ID 없는 키를 쓴다.
    assert event_key(_parse(fields() | {"eventId": "junk"})) == "evt:12:3:DOCUMENT_UPDATED"


def _parse(raw: dict[str, str]):
    from history_shared.events import DocumentEvent

    return DocumentEvent.from_fields(raw)

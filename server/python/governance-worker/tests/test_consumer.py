"""색인 완료 이벤트(stream:index-events, DOCUMENT_INDEXED)를 받아 상충 검사를 건다."""
import json
import uuid

from history_shared.streams import PermanentMessageError, StreamMessage

from app.consumer import DocumentIndexedHandler, build_consumer
from app.settings import Settings


def fields(**payload) -> dict[str, str]:
    body = {"documentId": 12, "version": 3, "chunkCount": 4, "organizationId": 1, "teamId": None} | payload
    return {"aggregateId": str(body["documentId"]), "eventType": "DOCUMENT_INDEXED", "payload": json.dumps(body)}


class RecordingDetector:
    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    async def detect_and_record(self, document_id: int, organization_id: int):
        self.calls.append((document_id, organization_id))
        return []


async def test_handler_dispatches_document_id_and_organization_id():
    detector = RecordingDetector()

    await DocumentIndexedHandler(detector)(StreamMessage("1-0", fields(organizationId=7)))

    assert detector.calls == [(12, 7)]


async def test_wrong_event_type_is_a_permanent_error():
    detector = RecordingDetector()

    try:
        await DocumentIndexedHandler(detector)(StreamMessage("1-0", {"eventType": "DOCUMENT_CREATED", "payload": "{}"}))
        assert False, "예외가 나야 한다"
    except PermanentMessageError:
        pass
    assert detector.calls == []


async def test_consumer_consumes_index_events_stream_and_acks(redis_client):
    stream = f"test:index-events:{uuid.uuid4().hex}"
    settings = Settings(index_events_stream=stream, consumer_name="gov-test")
    detector = RecordingDetector()
    consumer = build_consumer(redis_client, detector, settings)
    try:
        await consumer.ensure_group()
        await redis_client.xadd(stream, fields(documentId=55, organizationId=2))

        await consumer.poll()

        assert detector.calls == [(55, 2)]
        assert (await redis_client.xpending(stream, settings.consumer_group))["pending"] == 0
    finally:
        await redis_client.delete(stream, consumer.dlq_stream)

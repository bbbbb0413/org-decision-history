"""색인 완료 이벤트를 받아 상충 검사를 건다.

index-worker가 색인을 끝낸 뒤에만 indexing.document_chunk 본문을 읽을 수 있어,
문서 등록/수정 이벤트(stream:document-events) 대신 색인 완료 이벤트(stream:index-events,
DOCUMENT_INDEXED)를 구독한다. index-worker와 같은 history_shared.streams.RedisStreamConsumer를 써서
실패 항목 재처리(XAUTOCLAIM)와 DLQ를 함께 얻는다.
"""
import redis.asyncio as redis
import structlog
from history_shared.events import DocumentIndexedEvent, EventParseError
from history_shared.streams import PermanentMessageError, RedisStreamConsumer, StreamMessage

from app.detector import ConflictDetector
from app.settings import Settings

log = structlog.get_logger()


class DocumentIndexedHandler:
    def __init__(self, detector: ConflictDetector):
        self._detector = detector

    async def __call__(self, message: StreamMessage) -> None:
        try:
            event = DocumentIndexedEvent.from_fields(message.fields)
        except EventParseError as e:
            raise PermanentMessageError(str(e)) from e

        log.info(
            "색인 완료 이벤트 수신, 상충 검사 착수",
            documentId=event.document_id,
            version=event.version,
            chunkCount=event.chunk_count,
        )
        await self._detector.detect_and_record(event.document_id, event.organization_id)


def build_consumer(client: redis.Redis, detector: ConflictDetector, settings: Settings) -> RedisStreamConsumer:
    return RedisStreamConsumer(
        client,
        stream=settings.index_events_stream,
        group=settings.consumer_group,
        handler=DocumentIndexedHandler(detector),
        consumer=settings.consumer_name or None,
        claim_min_idle_ms=settings.stream_claim_min_idle_ms,
        max_deliveries=settings.stream_max_deliveries,
    )

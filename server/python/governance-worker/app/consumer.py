"""색인 결과 이벤트를 받아 상충 검사를 걸거나 삭제된 문서를 지식 그래프에서 뺀다.

index-worker가 색인을 끝낸 뒤에만 indexing.document_chunk 본문을 읽을 수 있어,
문서 등록/수정 이벤트(stream:document-events) 대신 색인 결과 이벤트(stream:index-events)를 구독한다.
- DOCUMENT_INDEXED: 상충 검사(detect_and_record)
- DOCUMENT_UNINDEXED: 삭제된 문서를 지식 그래프에서 제거(forget_document)
index-worker와 같은 history_shared.streams.RedisStreamConsumer를 써서 실패 항목 재처리(XAUTOCLAIM)와
DLQ를 함께 얻는다.
"""
import redis.asyncio as redis
import structlog
from history_shared.events import DocumentIndexedEvent, EventParseError, parse_index_event
from history_shared.streams import PermanentMessageError, RedisStreamConsumer, StreamMessage

from app.detector import ConflictDetector
from app.settings import Settings

log = structlog.get_logger()


class IndexEventHandler:
    def __init__(self, detector: ConflictDetector):
        self._detector = detector

    async def __call__(self, message: StreamMessage) -> None:
        try:
            event = parse_index_event(message.fields)
        except EventParseError as e:
            raise PermanentMessageError(str(e)) from e

        if isinstance(event, DocumentIndexedEvent):
            log.info(
                "색인 완료 이벤트 수신, 상충 검사 착수",
                documentId=event.document_id,
                version=event.version,
                chunkCount=event.chunk_count,
            )
            await self._detector.detect_and_record(event.document_id, event.organization_id)
            return

        log.info("색인 제거 이벤트 수신, 지식 그래프에서 문서 제거", documentId=event.document_id, version=event.version)
        await self._detector.forget_document(event.document_id)


def build_consumer(client: redis.Redis, detector: ConflictDetector, settings: Settings) -> RedisStreamConsumer:
    return RedisStreamConsumer(
        client,
        stream=settings.index_events_stream,
        group=settings.consumer_group,
        handler=IndexEventHandler(detector),
        consumer=settings.consumer_name or None,
        claim_min_idle_ms=settings.stream_claim_min_idle_ms,
        max_deliveries=settings.stream_max_deliveries,
    )

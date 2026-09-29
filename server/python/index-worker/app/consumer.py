"""문서 변경 이벤트를 색인 요청으로 바꾼다.

멱등은 두 겹이다. 소비자 그룹 ACK 로 재전달을 줄이고, index_job_item.event_key 유일 제약으로 중복 접수를 막는다.
실패 항목은 XAUTOCLAIM 으로 다시 가져와 처리하고, 계약을 어긴 항목은 DLQ 로 보낸다(history_shared.streams).
"""
import redis.asyncio as redis
import structlog
from history_shared.events import DocumentEvent, EventParseError
from history_shared.streams import PermanentMessageError, RedisStreamConsumer, StreamMessage

from app.jobs import IndexTrigger, JobService
from app.settings import Settings

log = structlog.get_logger()

_TRIGGER_BY_EVENT = {
    "DOCUMENT_CREATED": IndexTrigger.DOCUMENT_UPLOAD,
    "DOCUMENT_DELETED": IndexTrigger.DOCUMENT_DELETE,
}


def trigger_for(event_type: str) -> IndexTrigger:
    return _TRIGGER_BY_EVENT.get(event_type, IndexTrigger.DOCUMENT_UPDATE)


def event_key(event: DocumentEvent) -> str:
    """접수 중복을 막는 키. 같은 문서라도 수정과 삭제는 다른 처리이므로 종류까지 넣는다.

    아웃박스 행 번호(`event_id`)까지 넣는다. 그게 없으면 **같은 판에 파일을 다시 올린 경우**
    키가 앞 이벤트와 겹쳐서 `ON CONFLICT (event_key) DO NOTHING` 에 걸리고, 두 번째
    재색인이 조용히 건너뛰어진다 — 화면에는 새 파일이 보이는데 색인은 이전 내용을 들고 있게 된다.

    행 번호는 재전달에도 같은 값이므로(아웃박스 행 하나 = 이벤트 하나) 최소 한 번 전달에
    대한 멱등은 그대로다. 이 필드는 선택이라 없으면 문서·판·종류만으로 키를 만든다.
    """
    base = f"evt:{event.document_id}:{event.version}:{event.event_type}"
    return base if event.event_id is None else f"{base}:{event.event_id}"


class DocumentEventHandler:
    def __init__(self, jobs: JobService):
        self._jobs = jobs

    async def __call__(self, message: StreamMessage) -> None:
        try:
            event = DocumentEvent.from_fields(message.fields)
        except EventParseError as e:
            raise PermanentMessageError(str(e)) from e

        trigger = trigger_for(event.event_type)
        job_key = await self._jobs.accept(event.document_id, event.version, trigger, event_key(event))
        if job_key is not None:
            log.info(
                "색인 접수",
                documentId=event.document_id,
                version=event.version,
                trigger=str(trigger),
                jobKey=str(job_key),
            )


def build_consumer(client: redis.Redis, jobs: JobService, settings: Settings) -> RedisStreamConsumer:
    return RedisStreamConsumer(
        client,
        stream=settings.document_events_stream,
        group=settings.consumer_group,
        handler=DocumentEventHandler(jobs),
        consumer=settings.consumer_name or None,
        claim_min_idle_ms=settings.stream_claim_min_idle_ms,
        max_deliveries=settings.stream_max_deliveries,
    )

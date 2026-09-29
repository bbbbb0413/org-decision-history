"""색인 완료를 stream:index-events로 알린다. governance-worker가 구독한다."""
import redis.asyncio as redis
from history_shared.events import STREAM_INDEX_EVENTS, DocumentIndexedEvent


class RedisIndexEventPublisher:
    def __init__(self, client: redis.Redis, stream: str = STREAM_INDEX_EVENTS):
        self._redis = client
        self._stream = stream

    async def publish(self, event: DocumentIndexedEvent) -> None:
        await self._redis.xadd(self._stream, event.to_fields())

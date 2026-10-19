"""RedisAnswerSink 가 Stream 에 넣은 뒤 알림을 보내는지 (chat-service PUBSUB 릴레이의 발행 쪽).

chat-service 는 알림을 받으면 Stream 을 읽는다. 알림이 XADD 보다 먼저 나가면 읽을 게 아직 없어서
그 조각은 다음 알림이나 하트비트 주기 확인까지 늦어진다 — 순서를 고정한다.
"""
from app.redis_sink import RedisAnswerSink


class FakeRedis:
    def __init__(self):
        self.calls: list[tuple] = []
        self._seq = 0

    async def xadd(self, key, fields):
        self._seq += 1
        record_id = f"1700000000000-{self._seq}"
        self.calls.append(("xadd", key, fields, record_id))
        return record_id

    async def publish(self, channel, message):
        self.calls.append(("publish", channel, message))
        return 0

    async def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds))


async def test_publishes_after_xadd_with_record_id():
    redis = FakeRedis()
    sink = RedisAnswerSink(redis, "job-1")

    await sink.delta("안녕")

    assert [c[0] for c in redis.calls] == ["xadd", "publish"]
    _, stream, fields, record_id = redis.calls[0]
    assert stream == "chat:answer:stream:job-1"
    assert fields == {"event": "delta", "data": '{"text": "안녕"}'}
    assert redis.calls[1] == ("publish", "chat:answer:live:job-1", record_id)


async def test_done_notifies_then_sets_ttl():
    redis = FakeRedis()
    sink = RedisAnswerSink(redis, "job-1")

    await sink.done(7, "OK", "m", 10, [])

    assert [c[0] for c in redis.calls] == ["xadd", "publish", "expire"]
    assert redis.calls[0][2]["event"] == "done"
    assert redis.calls[2] == ("expire", "chat:answer:stream:job-1", 600)


async def test_every_event_is_notified():
    redis = FakeRedis()
    sink = RedisAnswerSink(redis, "job-1")

    await sink.sources([])
    await sink.graph({"nodes": []})
    await sink.delta("a")
    await sink.error("ANSWER_FAILED", "x")

    events = [c[2]["event"] for c in redis.calls if c[0] == "xadd"]
    published = [c[2] for c in redis.calls if c[0] == "publish"]
    assert events == ["sources", "graph", "delta", "error"]
    assert published == [c[3] for c in redis.calls if c[0] == "xadd"]

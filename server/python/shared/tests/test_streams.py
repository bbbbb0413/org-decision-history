import asyncio
import uuid

import pytest

from history_shared.streams import PermanentMessageError, RedisStreamConsumer, StreamMessage


@pytest.fixture
async def stream(redis_client):
    name = f"test:stream:{uuid.uuid4().hex}"
    yield name
    await redis_client.delete(name, f"{name}:dlq")


def consumer_for(redis_client, stream, handler, **overrides) -> RedisStreamConsumer:
    options = dict(group="g", consumer="c1", claim_min_idle_ms=0, block_ms=10)
    options.update(overrides)
    return RedisStreamConsumer(redis_client, stream=stream, handler=handler, **options)


async def pending_count(redis_client, stream) -> int:
    return (await redis_client.xpending(stream, "g"))["pending"]


async def test_success_is_acked_and_reads_entries_added_before_group(redis_client, stream):
    await redis_client.xadd(stream, {"k": "before-group"})
    seen: list[StreamMessage] = []

    async def handler(message):
        seen.append(message)

    consumer = consumer_for(redis_client, stream, handler)
    await consumer.ensure_group()
    await consumer.ensure_group()  # 이미 있어도 실패하지 않는다
    await redis_client.xadd(stream, {"k": "after-group"})

    assert await consumer.poll() == 2
    assert [m.fields["k"] for m in seen] == ["before-group", "after-group"]
    assert all(m.deliveries == 1 for m in seen)
    assert await pending_count(redis_client, stream) == 0


async def test_failed_entry_stays_pending_and_is_reclaimed(redis_client, stream):
    deliveries: list[int] = []

    async def handler(message):
        deliveries.append(message.deliveries)
        if len(deliveries) == 1:
            raise RuntimeError("일시 실패")

    consumer = consumer_for(redis_client, stream, handler)
    await consumer.ensure_group()
    await redis_client.xadd(stream, {"k": "v"})

    await consumer.poll()
    assert await pending_count(redis_client, stream) == 1

    assert await consumer.reclaim() == 1
    assert deliveries == [1, 2]
    assert await pending_count(redis_client, stream) == 0


async def test_other_consumer_takes_over_stale_entries(redis_client, stream):
    async def failing(message):
        raise RuntimeError("c1 은 처리 도중 죽었다")

    handled: list[str] = []

    async def succeeding(message):
        handled.append(message.id)

    first = consumer_for(redis_client, stream, failing, consumer="c1")
    await first.ensure_group()
    entry_id = await redis_client.xadd(stream, {"k": "v"})
    await first.poll()

    second = consumer_for(redis_client, stream, succeeding, consumer="c2")
    assert await second.reclaim() == 1
    assert handled == [entry_id]
    assert await pending_count(redis_client, stream) == 0


async def test_entry_moves_to_dlq_after_max_deliveries(redis_client, stream):
    attempts = 0

    async def handler(message):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("계속 실패")

    consumer = consumer_for(redis_client, stream, handler, max_deliveries=2)
    await consumer.ensure_group()
    entry_id = await redis_client.xadd(stream, {"eventType": "DOCUMENT_CREATED"})

    await consumer.poll()  # 1회
    await consumer.reclaim()  # 2회
    await consumer.reclaim()  # 3회째 전달 → 핸들러를 부르지 않고 DLQ

    assert attempts == 2
    assert await pending_count(redis_client, stream) == 0
    [(_, dead)] = await redis_client.xrange(consumer.dlq_stream)
    assert dead["eventType"] == "DOCUMENT_CREATED"
    assert dead["dlq.sourceId"] == entry_id
    assert dead["dlq.deliveries"] == "3"
    assert dead["dlq.group"] == "g"


async def test_permanent_error_goes_to_dlq_immediately(redis_client, stream):
    async def handler(message):
        raise PermanentMessageError("계약 위반")

    consumer = consumer_for(redis_client, stream, handler)
    await consumer.ensure_group()
    await redis_client.xadd(stream, {"k": "broken"})

    await consumer.poll()
    assert await pending_count(redis_client, stream) == 0
    [(_, dead)] = await redis_client.xrange(consumer.dlq_stream)
    assert dead["dlq.reason"] == "계약 위반"
    assert dead["dlq.deliveries"] == "1"


async def test_run_stops_when_event_is_set(redis_client, stream):
    stop = asyncio.Event()
    seen: list[str] = []

    async def handler(message):
        seen.append(message.fields["k"])
        stop.set()

    consumer = consumer_for(redis_client, stream, handler, block_ms=50)
    await redis_client.xadd(stream, {"k": "v"})
    await asyncio.wait_for(consumer.run(stop), timeout=5)
    assert seen == ["v"]


# ---- 소비 지표 ----


def sample(name: str, **labels) -> float:
    """prometheus_client 가 공개하는 조회 방식. 내부 속성(_value 등)에 손대지 않는다."""
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_processed_and_retried_and_dead_lettered_are_counted(redis_client, stream):
    """스트림 이름이 테스트마다 달라(uuid) 카운터가 다른 테스트와 섞이지 않는다."""
    fail_next = True

    async def handler(message):
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise RuntimeError("한 번 실패")

    consumer = consumer_for(redis_client, stream, handler, max_deliveries=2)
    await consumer.ensure_group()
    await redis_client.xadd(stream, {"k": "v"})

    await consumer.poll()      # 실패 → retried (ACK 하지 않고 남긴다)
    await consumer.reclaim()   # 다시 가져와 성공 → processed

    assert sample("stream_messages_total", stream=stream, group="g", outcome="retried") == 1
    assert sample("stream_messages_total", stream=stream, group="g", outcome="processed") == 1
    assert sample("stream_messages_total", stream=stream, group="g", outcome="dead_lettered") == 0
    # 실패한 처리도 소요 시간에 들어간다 — 타임아웃으로 죽는지 보려면 성공만 재서는 안 된다
    assert sample("stream_handler_seconds_count", stream=stream, group="g") == 2


async def test_dead_letter_is_counted_with_queue_depth(redis_client, stream):
    async def broken(message):
        raise PermanentMessageError("계약 위반")

    consumer = consumer_for(redis_client, stream, broken)
    await consumer.ensure_group()
    await redis_client.xadd(stream, {"k": "broken"})

    await consumer.poll()

    assert sample("stream_messages_total", stream=stream, group="g", outcome="dead_lettered") == 1
    # 핸들러를 부르지 않고 바로 DLQ 로 간 게 아니라(PermanentMessageError) 한 번 불렀으므로 관측된다
    assert sample("stream_handler_seconds_count", stream=stream, group="g") == 1
    assert sample("stream_dead_letter_depth", stream=stream) == 1

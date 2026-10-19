"""Redis Stream 소비자 그룹 공통 루프.

index-worker·governance-worker 가 함께 쓴다.

- 핸들러가 성공한 항목만 ACK 한다. 실패하면 대기 목록(PEL)에 남는다.
- claim_min_idle_ms 이상 대기한 항목은 XAUTOCLAIM 으로 가져와 다시 처리한다. 죽은 소비자의 항목도 이렇게 넘겨받는다.
- 전달 횟수가 max_deliveries 를 넘거나 핸들러가 PermanentMessageError 를 던지면 DLQ 스트림으로 옮기고 ACK 한다.

- 핸들러가 도는 동안에는 claim_min_idle_ms 의 1/3 간격으로 그 항목의 유휴 시간을 0 으로 되돌린다(XCLAIM JUSTID).
  처리가 claim_min_idle_ms 보다 오래 걸려도 다른 인스턴스가 살아 있는 처리를 가로채 두 번 돌리지 않는다.
  소유자가 이미 바뀌었으면(이 프로세스가 오래 멈췄던 경우) 되찾지 않고 갱신을 멈춘다.

같은 항목이 두 번 처리될 수 있으므로(ACK 직전 종료, 처리 도중 프로세스 종료 등) 핸들러는 멱등해야 한다.
"""
import asyncio
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import redis.asyncio as redis
import structlog
from redis.exceptions import ResponseError

from history_shared.metrics import STREAM_DEAD_LETTER_DEPTH, STREAM_HANDLER_SECONDS, STREAM_MESSAGES

log = structlog.get_logger()


@dataclass(frozen=True)
class StreamMessage:
    id: str
    fields: dict[str, str] = field(default_factory=dict)
    # 이 항목이 소비자에게 전달된 횟수. 처음 읽으면 1, 다시 가져올 때마다 1씩 는다
    deliveries: int = 1


class PermanentMessageError(Exception):
    """다시 시도해도 결과가 같은 실패(계약 위반 등). 재시도하지 않고 DLQ 로 보낸다."""


Handler = Callable[[StreamMessage], Awaitable[None]]


def default_consumer_name(group: str) -> str:
    """소비자 이름은 프로세스마다 달라야 한다. 같은 이름을 쓰면 서로의 대기 항목을 자기 것으로 본다."""
    return f"{group}-{socket.gethostname()}"


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _decode_fields(fields: Mapping | None) -> dict[str, str]:
    return {_text(k): _text(v) for k, v in (fields or {}).items()}


class RedisStreamConsumer:
    def __init__(
        self,
        client: redis.Redis,
        *,
        stream: str,
        group: str,
        handler: Handler,
        consumer: str | None = None,
        start_id: str = "0",
        batch_size: int = 32,
        block_ms: int = 2000,
        claim_min_idle_ms: int = 60_000,
        claim_interval_seconds: float = 30.0,
        max_deliveries: int = 5,
        dlq_stream: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._redis = client
        self._stream = stream
        self._group = group
        self._handler = handler
        self._consumer = consumer or default_consumer_name(group)
        # 그룹을 처음 만들 때의 시작 위치. "0" 이면 그룹 생성 전에 쌓인 항목부터 읽는다
        self._start_id = start_id
        self._batch_size = batch_size
        self._block_ms = block_ms
        self._claim_min_idle_ms = claim_min_idle_ms
        self._claim_interval = claim_interval_seconds
        self._max_deliveries = max_deliveries
        self._dlq_stream = dlq_stream or f"{stream}:dlq"
        self._clock = clock
        # 처리 중 항목의 유휴 시간 갱신 간격. 한 번 놓쳐도 회수 기준에 닿지 않게 기준의 1/3 로 둔다
        self._heartbeat_seconds = max(claim_min_idle_ms / 3000, 0.05)
        self._next_claim_at = float("-inf")  # 기동 직후 한 번 회수한다

    @property
    def consumer(self) -> str:
        return self._consumer

    @property
    def dlq_stream(self) -> str:
        return self._dlq_stream

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(self._stream, self._group, id=self._start_id, mkstream=True)
            log.info("스트림 소비자 그룹을 만들었다", stream=self._stream, group=self._group, start_id=self._start_id)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def run(self, stop: asyncio.Event) -> None:
        await self.ensure_group()
        log.info("스트림 소비 시작", stream=self._stream, group=self._group, consumer=self._consumer)
        while not stop.is_set():
            try:
                if self._clock() >= self._next_claim_at:
                    await self.reclaim()
                    self._next_claim_at = self._clock() + self._claim_interval
                await self.poll()
            except asyncio.CancelledError:
                raise
            except ResponseError as e:
                if "NOGROUP" in str(e):
                    log.warning("소비자 그룹이 사라져 다시 만든다", stream=self._stream, group=self._group)
                    await self.ensure_group()
                    continue
                log.error("스트림 읽기 실패. 잠시 후 다시 시도한다", stream=self._stream, error=str(e))
                await asyncio.sleep(1.0)
            except Exception as e:  # noqa: BLE001 — 연결 끊김 등. 루프를 멈추지 않는다
                log.error("스트림 읽기 실패. 잠시 후 다시 시도한다", stream=self._stream, error=str(e))
                await asyncio.sleep(1.0)
        log.info("스트림 소비 종료", stream=self._stream, group=self._group)

    async def poll(self) -> int:
        """새 항목을 한 번 읽어 처리한다. 처리한 항목 수를 돌려준다."""
        response = await self._redis.xreadgroup(
            self._group,
            self._consumer,
            {self._stream: ">"},
            count=self._batch_size,
            block=self._block_ms,
        )
        handled = 0
        for _stream, entries in response or []:
            for message_id, fields in entries:
                await self._process(StreamMessage(_text(message_id), _decode_fields(fields), 1))
                handled += 1
        return handled

    async def reclaim(self) -> int:
        """오래 대기한 항목을 모두 가져와 다시 처리한다. 처리한 항목 수를 돌려준다."""
        start, handled = "0-0", 0
        while True:
            result = await self._redis.xautoclaim(
                self._stream,
                self._group,
                self._consumer,
                min_idle_time=self._claim_min_idle_ms,
                start_id=start,
                count=self._batch_size,
            )
            cursor = _text(result[0])
            # 스트림에서 이미 지워진 항목은 Redis 7 이 대기 목록에서 빼고 따로 돌려준다. 여기서는 본문이 있는 것만 처리한다
            entries = [(_text(mid), fields) for mid, fields in result[1] if fields is not None]
            if entries:
                deliveries = await self._delivery_counts(entries[0][0], entries[-1][0])
                for message_id, fields in entries:
                    message = StreamMessage(message_id, _decode_fields(fields), deliveries.get(message_id, 1))
                    await self._process(message)
                handled += len(entries)
            if cursor == "0-0":
                return handled
            start = cursor

    async def _delivery_counts(self, first_id: str, last_id: str) -> dict[str, int]:
        pending = await self._redis.xpending_range(
            self._stream,
            self._group,
            min=first_id,
            max=last_id,
            count=10_000,
            consumername=self._consumer,
        )
        return {_text(p["message_id"]): int(p["times_delivered"]) for p in pending}

    def _count(self, outcome: str) -> None:
        STREAM_MESSAGES.labels(stream=self._stream, group=self._group, outcome=outcome).inc()

    async def _process(self, message: StreamMessage) -> None:
        if message.deliveries > self._max_deliveries:
            await self._dead_letter(message, f"전달 {message.deliveries}회로 한도 {self._max_deliveries}회를 넘었다")
            return
        started = time.monotonic()
        heartbeat = asyncio.create_task(self._keep_alive(message.id))
        try:
            await self._handler(message)
        except asyncio.CancelledError:
            raise
        except PermanentMessageError as e:
            await self._dead_letter(message, str(e) or "처리할 수 없는 항목")
            return
        except Exception as e:  # noqa: BLE001 — 실패 항목은 대기 목록에 남겨 다시 처리한다
            self._count("retried")
            log.warning(
                "스트림 항목 처리 실패. ACK 하지 않고 재처리 대상으로 남긴다",
                stream=self._stream,
                message_id=message.id,
                deliveries=message.deliveries,
                error=str(e),
            )
            return
        finally:
            heartbeat.cancel()
            # 실패한 처리도 얼마나 걸렸는지가 알고 싶은 정보다(타임아웃으로 죽는지 등)
            STREAM_HANDLER_SECONDS.labels(stream=self._stream, group=self._group).observe(
                time.monotonic() - started
            )
        await self._redis.xack(self._stream, self._group, message.id)
        self._count("processed")

    async def _keep_alive(self, message_id: str) -> None:
        """처리 중인 항목을 아직 이 소비자가 붙잡고 있다고 알린다. 실패해도 처리 자체는 계속한다."""
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                pending = await self._redis.xpending_range(
                    self._stream, self._group, min=message_id, max=message_id, count=1
                )
                if not pending or _text(pending[0]["consumer"]) != self._consumer:
                    log.warning(
                        "처리 중 항목의 소유가 바뀌어 갱신을 멈춘다",
                        stream=self._stream,
                        message_id=message_id,
                        owner=_text(pending[0]["consumer"]) if pending else None,
                    )
                    return
                await self._redis.xclaim(
                    self._stream, self._group, self._consumer, min_idle_time=0, message_ids=[message_id], justid=True
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 갱신 실패로 처리를 깨지 않는다. 다음 간격에 다시 한다
                log.debug("처리 중 항목 갱신 실패", stream=self._stream, message_id=message_id, error=str(e))

    async def _dead_letter(self, message: StreamMessage, reason: str) -> None:
        entry = dict(message.fields)
        entry.update(
            {
                "dlq.sourceStream": self._stream,
                "dlq.sourceId": message.id,
                "dlq.group": self._group,
                "dlq.deliveries": str(message.deliveries),
                "dlq.reason": reason[:500],
            }
        )
        # DLQ 기록과 ACK 를 함께 반영한다. 사이에 끊기면 항목이 사라지거나 DLQ 에 두 번 들어간다
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xadd(self._dlq_stream, entry)
            pipe.xack(self._stream, self._group, message.id)
            await pipe.execute()
        self._count("dead_lettered")
        # 운영 화면(auth-service Ops)이 같은 값을 직접 XLEN 으로 읽지만, 메트릭으로도 남겨
        # Prometheus 만 보고도 유실이 쌓이는 걸 알 수 있게 한다
        try:
            depth = await self._redis.xlen(self._dlq_stream)
            STREAM_DEAD_LETTER_DEPTH.labels(stream=self._stream).set(depth)
        except Exception as e:  # noqa: BLE001 — 메트릭 때문에 적재 흐름을 깨지 않는다
            log.debug("DLQ 깊이 관측 실패", dlq=self._dlq_stream, error=str(e))
        log.error(
            "스트림 항목을 DLQ 로 옮겼다",
            stream=self._stream,
            dlq=self._dlq_stream,
            message_id=message.id,
            deliveries=message.deliveries,
            reason=reason,
        )

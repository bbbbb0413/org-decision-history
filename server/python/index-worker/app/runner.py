"""접수된 색인 항목을 처리한다.

한 번 깨어날 때 여러 건을 이어서 처리한다. 건마다 주기를 기다리면 일괄 색인 100건에 주기의 100배가 걸린다.
다만 상한을 둬 다른 작업이 굶지 않게 한다.
"""
import asyncio
import time
from collections.abc import Callable
from typing import Protocol

import structlog

log = structlog.get_logger()


class Jobs(Protocol):
    async def process_next(self) -> bool: ...

    async def recover_stale_running(self) -> int: ...


class JobRunner:
    def __init__(
        self,
        jobs: Jobs,
        *,
        interval_seconds: float = 1.0,
        max_per_tick: int = 20,
        recover_interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._jobs = jobs
        self._interval = interval_seconds
        self._max_per_tick = max_per_tick
        self._recover_interval = recover_interval_seconds
        self._clock = clock
        self._next_recover_at = float("-inf")

    async def run(self, stop: asyncio.Event) -> None:
        log.info("색인 러너 시작", maxPerTick=self._max_per_tick)
        while not stop.is_set():
            try:
                if self._clock() >= self._next_recover_at:
                    await self._jobs.recover_stale_running()
                    self._next_recover_at = self._clock() + self._recover_interval
                await self.drain()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — DB 연결 끊김 등. 다음 주기에 다시 시도한다
                log.error("색인 처리 중 예기치 못한 오류", error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
        log.info("색인 러너 종료")

    async def drain(self) -> int:
        processed = 0
        while processed < self._max_per_tick:
            if not await self._jobs.process_next():
                break
            processed += 1
        if processed:
            log.debug("색인 처리", count=processed)
        return processed

import asyncio

import app.main as main
from app.runner import JobRunner


class FakeJobs:
    def __init__(self, pending: int, *, fail_first: bool = False):
        self.pending = pending
        self.fail_first = fail_first
        self.recovered = 0

    async def process_next(self) -> bool:
        if self.fail_first:
            self.fail_first = False
            raise ConnectionError("db down")
        if self.pending == 0:
            return False
        self.pending -= 1
        return True

    async def recover_stale_running(self) -> int:
        self.recovered += 1
        return 0


async def test_drain_stops_at_max_per_tick():
    jobs = FakeJobs(pending=45)
    runner = JobRunner(jobs, max_per_tick=20)

    assert [await runner.drain() for _ in range(4)] == [20, 20, 5, 0]


async def run_briefly(runner: JobRunner, seconds: float = 0.1) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run(stop))
    await asyncio.sleep(seconds)
    stop.set()
    await asyncio.wait_for(task, timeout=1)


async def test_run_recovers_stale_items_then_keeps_processing_after_errors():
    jobs = FakeJobs(pending=30, fail_first=True)

    await run_briefly(JobRunner(jobs, interval_seconds=0.01, max_per_tick=20, recover_interval_seconds=60))

    assert jobs.pending == 0
    assert jobs.recovered == 1, "멈춘 항목 회수는 주기마다가 아니라 recover_interval 마다 한다"


async def test_supervise_restarts_crashed_loop(monkeypatch):
    monkeypatch.setattr(main, "_RESTART_DELAY_SECONDS", 0.01)
    stop = asyncio.Event()
    calls = 0

    async def flaky(stop_event: asyncio.Event) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("redis down")
        stop_event.set()

    await asyncio.wait_for(main._supervise("flaky", flaky, stop), timeout=1)

    assert calls == 3

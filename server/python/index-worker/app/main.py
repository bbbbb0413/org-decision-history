"""부팅 진입점. HTTP API(uvicorn)와 같은 이벤트 루프에서 스트림 소비자와 작업 러너를 돌린다."""
import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import redis.asyncio as redis
import structlog
import uvicorn
from fastapi import FastAPI
from history_shared.db import create_pool
from history_shared.embeddings import create_embedder
from history_shared.logging import configure_logging
from history_shared.vector import ChunkVectorStore

from app.api import ApiServices, create_app
from app.chunks_store import ChunkStore
from app.consumer import build_consumer
from app.documents import DocumentReader
from app.events_publisher import RedisIndexEventPublisher
from app.jobs import JobService
from app.pipeline import IndexingPipeline
from app.queries import IndexQueries
from app.runner import JobRunner
from app.settings import Settings, settings
from app.storage import ObjectStorage

configure_logging("index-worker")
log = structlog.get_logger()

_RESTART_DELAY_SECONDS = 5.0


async def _supervise(name: str, run: Callable[[asyncio.Event], Awaitable[None]], stop: asyncio.Event) -> None:
    """백그라운드 루프가 예외로 끝나면(기동 직후 Redis 연결 실패 등) 조용히 멈추지 않게 다시 띄운다."""
    while not stop.is_set():
        try:
            await run(stop)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("백그라운드 작업이 멈춰 다시 시작한다", task=name, error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=_RESTART_DELAY_SECONDS)
            except TimeoutError:
                pass


def build_app(cfg: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await create_pool(cfg)
        redis_client = redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
            decode_responses=True,
        )
        embedder = create_embedder(cfg)
        vectors = ChunkVectorStore(cfg)
        await vectors.ensure_collection()

        documents = DocumentReader(pool)
        events = RedisIndexEventPublisher(redis_client)
        pipeline = IndexingPipeline(documents, ObjectStorage(cfg), embedder, vectors, ChunkStore(pool), events)
        jobs = JobService(pool, pipeline, running_timeout=timedelta(minutes=cfg.running_timeout_minutes))
        app.state.services = ApiServices(jobs=jobs, queries=IndexQueries(pool), documents=documents)

        stop = asyncio.Event()
        tasks = []
        if cfg.consumer_enabled:
            tasks.append(asyncio.create_task(_supervise("consumer", build_consumer(redis_client, jobs, cfg).run, stop)))
        if cfg.runner_enabled:
            runner = JobRunner(jobs, interval_seconds=cfg.runner_interval_seconds, max_per_tick=cfg.runner_max_per_tick)
            tasks.append(asyncio.create_task(_supervise("runner", runner.run, stop)))
        log.info("index-worker 시작", consumer=cfg.consumer_enabled, runner=cfg.runner_enabled, model=embedder.model_name)
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await embedder.close()
            await vectors.close()
            await redis_client.aclose()
            await pool.close()
            log.info("index-worker 종료")

    return create_app(cfg, lifespan=lifespan)


def main() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=settings.server_port, access_log=False, log_level="info")


if __name__ == "__main__":
    main()

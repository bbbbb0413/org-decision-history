"""거버넌스 및 지식그래프 워커(governance-worker) 메인 진입점."""
import asyncio
import signal
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
import structlog
from history_shared.db import create_pool
from history_shared.embeddings import create_embedder
from history_shared.logging import configure_logging
from history_shared.metrics import start_metrics_server

from app.chunk_reader import ChunkContentReader
from app.consumer import build_consumer
from app.db import Db
from app.detector import ConflictDetector
from app.generation import AnswerGenerator
from history_shared.graph.client import Neo4jGraphClient
from history_shared.graph.extractor import DecisionGraphExtractor
from history_shared.graph.sync import GraphSyncService
from app.notifications.factory import create_notification_dispatcher
from app.settings import settings
from app.vector_retriever import VectorSearchClient

configure_logging("governance-worker")
log = structlog.get_logger()

_RESTART_DELAY_SECONDS = 5.0


async def _supervise(name: str, run: Callable[[asyncio.Event], Awaitable[None]], stop: asyncio.Event) -> None:
    """소비 루프가 예외로 끝나면(기동 직후 Redis 연결 실패 등) 조용히 멈추지 않게 다시 띄운다."""
    while not stop.is_set():
        try:
            await run(stop)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("소비 루프가 멈춰 다시 시작한다", task=name, error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=_RESTART_DELAY_SECONDS)
            except TimeoutError:
                pass


async def main() -> None:
    log.info("governance-worker 시작 중...")

    # HTTP 앱이 없으니 메트릭만 내는 작은 서버를 따로 띄운다.
    # compose 에서 이 포트를 공개하지 않아 도커 네트워크 안에서만 닿는다.
    if settings.metrics_enabled:
        start_metrics_server(settings.metrics_port, "governance-worker")
        log.info("메트릭 서버 시작", port=settings.metrics_port)

    # DB 연결
    pool = await create_pool(settings, min_size=1, max_size=5, command_timeout=30.0)
    db = Db(pool)
    chunk_reader = ChunkContentReader(pool)

    # Redis 연결
    redis_client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        decode_responses=True,
    )

    # 임베딩, 검색, LLM
    # 키가 없으면 0 벡터 대신 index-worker 와 같은 대체 임베딩을 쓴다(contracts/indexing/README.md §10)
    embedder = create_embedder(settings)
    vector_client = VectorSearchClient(settings, chunk_reader)
    generator = AnswerGenerator(settings)

    # 지식 그래프 (Neo4j)
    graph_sync = None
    graph_client = None
    if settings.neo4j_enabled:
        try:
            graph_client = Neo4jGraphClient(
                uri=settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
            )
            await graph_client.connect()
            extractor = DecisionGraphExtractor(generator)
            graph_sync = GraphSyncService(graph_client, extractor)
            log.info("Neo4j 지식 그래프 동기화 서비스 초기화 완료")
        except Exception as e:
            log.warning("Neo4j 연결 실패 (지식 그래프 동기화 비활성화)", error=str(e))

    # 알림 디스패처
    dispatcher = create_notification_dispatcher(settings)

    # 상충 감지기
    detector = ConflictDetector(
        db=db,
        embedder=embedder,
        vector_client=vector_client,
        generator=generator,
        threshold=settings.notification_min_severity,
        dispatcher=dispatcher,
        frontend_base_url=settings.frontend_base_url,
        sync_service=graph_sync,
        settings=settings,
    )

    # 이벤트 컨슈머. index-worker의 색인 완료 이벤트(stream:index-events)를 구독한다
    consumer = build_consumer(redis_client, detector, settings)
    await consumer.ensure_group()

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("종료 시그널 수신, 거버넌스 워커를 정리합니다...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    consumer_task = asyncio.create_task(_supervise("consumer", consumer.run, stop_event))
    log.info("governance-worker 가동 준비 완료 (이벤트 대기 중)", stream=settings.index_events_stream)

    await stop_event.wait()

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    if graph_client:
        await graph_client.close()
    await embedder.close()
    await vector_client.close()
    await generator.close()
    await redis_client.aclose()
    await pool.close()
    log.info("governance-worker 정상 종료")


if __name__ == "__main__":
    asyncio.run(main())

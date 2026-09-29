"""부팅 진입점. HTTP 서버는 열지 않는다 —
Kafka consumer 두 개(job 처리 / DLT)만 돌리는 프로세스다.
"""
import asyncio
import signal

import structlog
from history_shared.logging import configure_logging
from history_shared.metrics import start_metrics_server

from app.dependencies import Dependencies
from app.worker import run_dlt_consumer, run_job_consumer

configure_logging("rag-service")
log = structlog.get_logger()


async def main() -> None:
    deps = await Dependencies().start()
    # HTTP 앱이 없으니 메트릭만 내는 작은 서버를 따로 띄운다.
    # compose 에서 이 포트를 공개하지 않아 도커 네트워크 안에서만 닿는다.
    if deps.settings.metrics_enabled:
        start_metrics_server(deps.settings.metrics_port, "rag-service")
        log.info("메트릭 서버 시작", port=deps.settings.metrics_port)
    log.info("rag-service 시작")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(run_job_consumer(deps)),
        asyncio.create_task(run_dlt_consumer(deps)),
    ]

    await stop.wait()
    log.info("종료 신호 수신. 정리 중")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await deps.close()


if __name__ == "__main__":
    asyncio.run(main())

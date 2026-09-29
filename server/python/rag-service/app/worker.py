"""Kafka job 소비.

두 컨슈머(job 처리 / DLT)를 이 프로세스 안에서 asyncio 태스크 두 개로 돌린다.
오프셋은 레코드 처리가 끝난 뒤에만 커밋한다 — 처리 도중 프로세스가 죽으면
커밋되지 않은 오프셋이 재전달된다. 재시도 백오프까지 갖춘 체인은 아니지만
최소한의 안전망 역할을 한다(가장 흔한 실패는 이미 그래프 내부의
catch-all에서 FAILED로 흡수된다).
"""
import time

import structlog
from aiokafka import AIOKafkaConsumer
from history_shared.logging import correlation_scope

from app.db import AnswerJob
from app.dependencies import Dependencies
from app.redis_sink import RedisAnswerSink

log = structlog.get_logger()


def _header_value(record, name: str) -> str | None:
    for key, value in record.headers or []:
        if key == name:
            return value.decode("utf-8")
    return None


async def run_job_consumer(deps: Dependencies) -> None:
    consumer = AIOKafkaConsumer(
        deps.settings.answer_jobs_topic,
        bootstrap_servers=deps.settings.kafka_bootstrap_servers,
        group_id=deps.settings.kafka_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    log.info(
        "답변 job 컨슈머 시작",
        topic=deps.settings.answer_jobs_topic,
        group=deps.settings.kafka_consumer_group,
    )
    try:
        async for record in consumer:
            job_key = record.value.decode("utf-8")
            correlation_id = _header_value(record, "X-Correlation-Id")
            # 원래 요청의 상관관계 ID를 이 job 처리 전체의 로깅 컨텍스트에 되살린다 —
            # 그러지 않으면 이 job이 남기는 감사 기록이 원래 브라우저 요청과 다른
            # ID로 끊겨 보인다.
            with correlation_scope(correlation_id):
                await _process_job(deps, job_key)
            await consumer.commit()
    finally:
        await consumer.stop()


async def run_dlt_consumer(deps: Dependencies) -> None:
    """job을 집어드는 단계(Postgres 조회·markRunning) 자체가 재시도 끝에도 계속
    실패해 DLT로 넘어온 job을 정리한다. 그래프 내부의 catch-all은 이미 답변
    생성(검색·LLM) 실패를 흡수하므로, 여기까지 오는 건 그 바깥의 실패다."""
    consumer = AIOKafkaConsumer(
        deps.settings.answer_jobs_dlt_topic,
        bootstrap_servers=deps.settings.kafka_bootstrap_servers,
        group_id=deps.settings.kafka_dlt_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    log.info(
        "답변 job DLT 컨슈머 시작",
        topic=deps.settings.answer_jobs_dlt_topic,
        group=deps.settings.kafka_dlt_consumer_group,
    )
    try:
        async for record in consumer:
            job_key = record.value.decode("utf-8")
            await _handle_dead_letter(deps, job_key)
            await consumer.commit()
    finally:
        await consumer.stop()


async def _process_job(deps: Dependencies, job_key: str) -> None:
    job: AnswerJob | None = await deps.db.find_job(job_key)
    if job is None:
        log.warning("답변 job을 찾을 수 없다. 이미 처리됐거나 데이터가 어긋난 상태다", job_key=job_key)
        return

    await deps.db.mark_running(job_key)
    sink = RedisAnswerSink(deps.redis, job_key)

    initial_state = {
        "user_id": job.user_id,
        "conversation_id": job.conversation_id,
        "question": job.question,
        "started_at": time.monotonic(),
    }

    final_state: dict = {}
    try:
        # custom: 노드가 get_stream_writer()로 내보낸 sources/delta/done 이벤트.
        # values: 매 노드 실행 뒤의 전체 상태 스냅샷 — 마지막 것만 남겨 message_id를 읽는다.
        async for mode, payload in deps.graph.astream(initial_state, stream_mode=["custom", "values"]):
            if mode == "custom":
                await _relay(sink, payload)
            elif mode == "values":
                final_state = payload
    except Exception as e:  # noqa: BLE001 — 검색·저장 등 파이프라인 자체가 깨진 경우
        # AskService.ask() 내부에서 생성 실패는 이미 LOW_CONFIDENCE로 흡수하므로, 여기까지
        # 올라오는 예외는 파이프라인 자체가 깨진 경우다. 재시도해도 같은 결과일 가능성이
        # 높아 job은 실패로 확정하고, 커넥션 쪽에 알려 화면이 무한 대기하지 않게 한다.
        log.error("답변 생성 job 처리 실패", job_key=job_key, error=str(e))
        await deps.db.mark_failed(job_key)
        await sink.error("ANSWER_FAILED", "답변을 만들지 못했습니다.")
        return

    message_id = final_state.get("message_id")
    if message_id is None:
        log.error("그래프가 message_id 없이 끝났다", job_key=job_key)
        await deps.db.mark_failed(job_key)
        await sink.error("ANSWER_FAILED", "답변을 만들지 못했습니다.")
        return
    await deps.db.mark_completed(job_key, message_id)


async def _relay(sink: RedisAnswerSink, item: dict) -> None:
    event = item["event"]
    data = item["data"]
    if event == "sources":
        await sink.sources(data["sources"])
    elif event == "graph":
        await sink.graph(data)
    elif event == "delta":
        await sink.delta(data["text"])
    elif event == "done":
        await sink.done(
            data["messageId"],
            data["answerStatus"],
            data["modelName"],
            data["latencyMs"],
            data["suggestions"],
            data.get("resumeAt"),
        )
    elif event == "error":
        await sink.error(data["code"], data["message"])


async def _handle_dead_letter(deps: Dependencies, job_key: str) -> None:
    try:
        job = await deps.db.find_job(job_key)
    except Exception as e:  # noqa: BLE001 — jobKey 형식이 잘못됐거나 DB 조회 자체가 실패
        log.warning("DLT 메시지 처리 중 조회 실패", job_key=job_key, error=str(e))
        return
    if job is None:
        log.warning("DLT로 넘어온 답변 job을 찾을 수 없다", job_key=job_key)
        return
    # 이미 다른 경로로 끝난 job이면(드물지만 재처리 중 완료됐을 수 있다) 건드리지 않는다.
    if job.status in ("COMPLETED", "FAILED"):
        return

    log.error("답변 job이 재시도 끝에도 처리되지 못해 DLT로 넘어왔다. 실패로 확정한다", job_key=job_key)
    await deps.db.mark_failed(job_key)
    await RedisAnswerSink(deps.redis, job_key).error("ANSWER_FAILED", "답변을 만들지 못했습니다.")

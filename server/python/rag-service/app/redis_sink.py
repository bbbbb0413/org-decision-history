"""
워커는 브라우저 커넥션을 직접 들고 있지 않다. 이벤트를 Redis Stream에 XADD하면
커넥션을 든 chat-service(Spring)가 그 스트림을 읽어 SSE로 relay한다.
이벤트 JSON 모양이 두 서비스 사이의 계약이다 — 언어 경계를 끊어주는 와이어 포맷이다.

XADD 뒤에 같은 job 의 알림 채널로 PUBLISH 한다. 알림은 "새 레코드 있음" 신호일 뿐이고
데이터는 언제나 Stream 에서 읽는다(chat.sse.relay-mode=PUBSUB). 구독자가 없으면 PUBLISH 는
아무 일도 하지 않으므로 chat-service 가 POLL 이어도 무해하다.
"""
import json

import redis.asyncio as redis

_STREAM_TTL_SECONDS = 600  # 10분


def _stream_key(job_key: str) -> str:
    return f"chat:answer:stream:{job_key}"


def _channel_key(job_key: str) -> str:
    return f"chat:answer:live:{job_key}"


class RedisAnswerSink:
    def __init__(self, redis_client: redis.Redis, job_key: str):
        self._redis = redis_client
        self._stream_key = _stream_key(job_key)
        self._channel = _channel_key(job_key)

    async def sources(self, sources: list[dict]) -> None:
        await self._send("sources", {"sources": sources})

    async def graph(self, graph_data: dict) -> None:
        await self._send("graph", graph_data)

    async def delta(self, text: str) -> None:
        await self._send("delta", {"text": text})

    async def done(
        self,
        message_id: int,
        answer_status: str,
        model_name: str | None,
        latency_ms: int,
        suggestions: list[str],
        resume_at: str | None = None,
    ) -> None:
        """resume_at은 이미 ISO-8601 문자열로 포맷된 값을 그대로 받는다
        (graph/nodes.py의 _emit_done이 한 번만 포맷한다 — 여기서 다시 계산하지 않는다)."""
        await self._send(
            "done",
            {
                "messageId": message_id,
                "answerStatus": answer_status,
                "modelName": model_name or "",
                "latencyMs": latency_ms,
                "suggestions": suggestions,
                "resumeAt": resume_at,
            },
        )
        await self._redis.expire(self._stream_key, _STREAM_TTL_SECONDS)

    async def error(self, code: str, message: str) -> None:
        await self._send("error", {"code": code, "message": message})
        await self._redis.expire(self._stream_key, _STREAM_TTL_SECONDS)

    async def _send(self, event: str, data: dict) -> None:
        # 순서가 중요하다: 알림을 받은 쪽이 읽을 때 레코드가 이미 Stream 에 있어야 한다.
        # PUBLISH 전에 죽어 알림이 빠져도 chat-service 의 하트비트 주기 확인이 읽어 간다.
        record_id = await self._redis.xadd(
            self._stream_key, {"event": event, "data": json.dumps(data, ensure_ascii=False)}
        )
        await self._redis.publish(self._channel, record_id)

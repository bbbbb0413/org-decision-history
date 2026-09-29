"""
워커는 브라우저 커넥션을 직접 들고 있지 않다. 이벤트를 Redis Stream에 XADD하면
커넥션을 든 chat-service(Spring)가 그 스트림을 tail해서 SSE로 relay한다.
이벤트 JSON 모양이 두 서비스 사이의 계약이다 — 언어 경계를 끊어주는 와이어 포맷이다.
"""
import json

import redis.asyncio as redis

_STREAM_TTL_SECONDS = 600  # 10분


def _stream_key(job_key: str) -> str:
    return f"chat:answer:stream:{job_key}"


class RedisAnswerSink:
    def __init__(self, redis_client: redis.Redis, job_key: str):
        self._redis = redis_client
        self._stream_key = _stream_key(job_key)

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
        await self._redis.xadd(self._stream_key, {"event": event, "data": json.dumps(data, ensure_ascii=False)})

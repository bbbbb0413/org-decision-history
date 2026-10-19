"""권한 범위별 답변 캐시.

키에 권한 스코프 해시를 넣는다. 같은 질문이라도 볼 수 있는 문서가 다르면
답이 달라야 하고, 캐시를 공유하면 그 자체가 정보 유출이다.
"""
import hashlib
import re

import redis.asyncio as redis

_WHITESPACE = re.compile(r"\s+")


class AnswerCache:
    def __init__(self, redis_client: redis.Redis, ttl_seconds: int = 3600):
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    async def find(self, question: str, scope_keys: list[str]) -> str | None:
        return await self._redis.get(self._key(question, scope_keys))

    async def put(self, question: str, scope_keys: list[str], answer: str) -> None:
        if not answer or not answer.strip():
            return
        await self._redis.set(self._key(question, scope_keys), answer, ex=self._ttl_seconds)

    def _key(self, question: str, scope_keys: list[str]) -> str:
        return f"answer:{self._hash(self._normalize(question))}:{self._hash(','.join(scope_keys))}"

    def _normalize(self, question: str) -> str:
        """공백과 대소문자 차이로 캐시가 갈리지 않게 한다."""
        return _WHITESPACE.sub(" ", question.lower()).strip()

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

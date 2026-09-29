"""임베딩. 문서(색인)와 질의(검색)가 한 구현을 쓰게 해 벡터 공간이 어긋나지 않게 한다.

규약: contracts/indexing/README.md §10.
- Retry-After 는 초와 HTTP 날짜 형식을 모두 해석한다.
- 분당 한도보다 큰 요청은 한도만큼만 기다린다. 버킷이 가득 차도 채울 수 없는 양을 기다리면 영원히 멈춘다.
- API 키가 없으면 문서·질의 모두 같은 대체 임베딩을 쓴다(벡터 공간이 어긋나지 않게).
"""
import asyncio
import email.utils
import hashlib
import re
import struct
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone

import httpx
import structlog

from history_shared.config import EmbeddingSettings
from history_shared.text import estimate_tokens, java_strip

log = structlog.get_logger()

DIMENSION = 768
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"
LOCAL_MODEL_NAME = "local-hashing-ngram"

_MAX_RETRIES = 3
_MAX_WAIT_SLICE_SECONDS = 5.0
_ASCII_WHITESPACE_RUN = re.compile(r"\s+", re.ASCII)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class EmbeddingError(Exception):
    """임베딩 실패. retryable 이 False 면 다시 시도해도 결과가 같다."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class Embedder(ABC):
    model_name: str

    @abstractmethod
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """입력과 같은 순서의 벡터 목록."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """검색 질의 벡터. 문서와 taskType 이 다르다."""

    async def close(self) -> None:
        return None


class TokenBucket:
    """분당 한도를 보내기 전에 조절한다. 429 를 받고 물러나는 것보다 한도를 아낀다."""

    def __init__(self, per_minute: int, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep):
        self._capacity = float(per_minute)
        self._refill_per_second = per_minute / 60.0
        self._available = float(per_minute)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, permits: int) -> None:
        # 한도보다 큰 요청은 버킷이 가득 차도 채워지지 않는다. 한도만큼만 요구해 영원히 기다리지 않게 한다.
        need = min(float(permits), self._capacity)
        async with self._lock:
            while True:
                now = self._clock()
                self._available = min(self._capacity, self._available + (now - self._last) * self._refill_per_second)
                self._last = now
                if self._available >= need:
                    self._available -= need
                    return
                wait = (need - self._available) / self._refill_per_second
                await self._sleep(min(wait, _MAX_WAIT_SLICE_SECONDS))


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    """Retry-After 헤더(초 또는 HTTP 날짜)를 대기 초로 바꾼다. 없거나 0 이하면 None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        seconds = float(value)
        return seconds if seconds > 0 else None
    try:
        at = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    delta = (at - (now or datetime.now(timezone.utc))).total_seconds()
    return delta if delta > 0 else None


class GeminiEmbedder(Embedder):
    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ):
        self._settings = settings
        self.model_name = settings.embedding_model
        self._client = client or httpx.AsyncClient(
            base_url=settings.embedding_endpoint, timeout=settings.embedding_timeout_seconds
        )
        self._sleep = sleep
        self._requests = TokenBucket(settings.embedding_requests_per_minute, clock=clock, sleep=sleep)
        self._tokens = TokenBucket(settings.embedding_tokens_per_minute, clock=clock, sleep=sleep)

    async def close(self) -> None:
        await self._client.aclose()

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        batch_size = max(1, self._settings.embedding_batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(await self._embed_batch(texts[start : start + batch_size], TASK_DOCUMENT))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_batch([text], TASK_QUERY))[0]

    async def _embed_batch(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        await self._requests.acquire(1)
        await self._tokens.acquire(sum(estimate_tokens(t) for t in texts))

        model = self._settings.embedding_model
        body = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": self._settings.embedding_dimension,
                }
                for text in texts
            ]
        }
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self._client.post(
                    f"/v1beta/models/{model}:batchEmbedContents",
                    params={"key": self._settings.gemini_api_key},
                    json=body,
                )
            except httpx.HTTPError as e:
                # 연결 실패·타임아웃. 안에서 다시 시도하지 않고 호출자(작업 재시도)에 맡긴다.
                # 예외 문구에 API 키가 담긴 URL 이 섞일 수 있어 종류만 남긴다.
                raise EmbeddingError(f"임베딩 요청 실패: {type(e).__name__}", retryable=True) from None

            status = response.status_code
            if 200 <= status < 300:
                return self._vectors(response, len(texts))
            retryable = status == 429 or status >= 500
            if not retryable or attempt == _MAX_RETRIES:
                raise EmbeddingError(f"임베딩 요청 실패 ({status})", retryable=retryable)
            wait = retry_after_seconds(response.headers.get("Retry-After")) or float(2**attempt)
            log.warning("임베딩 요청 한도. 대기 후 재시도한다", status=status, wait_seconds=wait, attempt=attempt + 1)
            await self._sleep(wait)
        raise EmbeddingError("임베딩 재시도를 모두 소진했다", retryable=True)

    @staticmethod
    def _vectors(response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            embeddings = response.json().get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != expected:
                raise ValueError("개수 불일치")
            return [[float(v) for v in item["values"]] for item in embeddings]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise EmbeddingError("임베딩 응답 형식이 예상과 다르다", retryable=False) from None


class LocalHashingEmbedder(Embedder):
    """API 키가 없을 때 쓰는 대체 임베딩. 파이프라인 확인용이며 의미 검색이 되지 않는다.

    계약에 정한 벡터를 그대로 만든다(contracts/indexing/golden 의 local-embeddings 로 확인).
    키를 넣어 Gemini 로 바꾸면 기존 벡터를 모두 다시 만들어야 한다.
    """

    model_name = LOCAL_MODEL_NAME
    _NGRAM = 3

    def __init__(self, dimension: int = DIMENSION):
        self._dimension = dimension
        log.warning("GEMINI_API_KEY 가 비어 있어 대체 임베딩으로 동작한다. 의미 기반 검색 품질을 기대할 수 없다")

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.vectorize(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self.vectorize(text)

    def vectorize(self, text: str) -> list[float]:
        normalized = java_strip(_ASCII_WHITESPACE_RUN.sub(" ", text.lower()))
        units = normalized.encode("utf-16-le", "surrogatepass")
        counts = [0.0] * self._dimension
        for i in range(len(units) // 2 - self._NGRAM + 1):
            gram = units[2 * i : 2 * (i + self._NGRAM)].decode("utf-16-le", "surrogatepass")
            # 계약은 짝 없는 서로게이트를 ? 로 바꾼다(Java getBytes(UTF_8) 의미). errors="replace" 가 같은 결과를 낸다
            digest = hashlib.md5(gram.encode("utf-8", "replace")).digest()
            counts[int.from_bytes(digest[:4], "big", signed=True) % self._dimension] += 1.0

        norm = sum(v * v for v in counts) ** 0.5
        if norm == 0:
            return counts
        norm32 = _float32(norm)
        return [_float32(v / norm32) for v in counts]


def _float32(value: float) -> float:
    """계약의 값이 단정밀도(float) 나눗셈 결과라 같게 반올림한다."""
    return struct.unpack("f", struct.pack("f", value))[0]


def create_embedder(settings: EmbeddingSettings) -> Embedder:
    """키가 있으면 Gemini, 없으면 대체 임베딩. 키가 없다고 파이프라인 전체를 멈추지 않는다."""
    if settings.has_gemini_key:
        return GeminiEmbedder(settings)
    return LocalHashingEmbedder(settings.embedding_dimension)

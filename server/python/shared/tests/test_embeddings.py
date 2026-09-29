import json
from datetime import datetime, timezone

import httpx
import pytest

from history_shared.config import EmbeddingSettings
from history_shared.embeddings import (
    TASK_DOCUMENT,
    TASK_QUERY,
    EmbeddingError,
    GeminiEmbedder,
    LocalHashingEmbedder,
    TokenBucket,
    create_embedder,
    retry_after_seconds,
)

API_KEY = "test-key-should-not-leak"


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def ok_response(request: httpx.Request) -> httpx.Response:
    texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
    return httpx.Response(200, json={"embeddings": [{"values": [float(len(t)), 1.0]} for t in texts]})


def build(handler, **overrides) -> tuple[GeminiEmbedder, FakeTime]:
    fake = FakeTime()
    settings = EmbeddingSettings(gemini_api_key=API_KEY, embedding_endpoint="https://gemini.test", **overrides)
    client = httpx.AsyncClient(base_url="https://gemini.test", transport=httpx.MockTransport(handler))
    return GeminiEmbedder(settings, client=client, sleep=fake.sleep, clock=fake.clock), fake


async def test_documents_are_batched_with_document_task_type():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return ok_response(request)

    embedder, fake = build(handler)
    texts = ["x" * (i + 1) for i in range(45)]
    vectors = await embedder.embed_documents(texts)

    assert [v[0] for v in vectors] == [float(len(t)) for t in texts]  # 순서 유지
    bodies = [json.loads(r.content)["requests"] for r in requests]
    assert [len(b) for b in bodies] == [20, 20, 5]
    for request, body in zip(requests, bodies):
        assert request.url.path == "/v1beta/models/gemini-embedding-001:batchEmbedContents"
        assert request.url.params["key"] == API_KEY
        for item in body:
            assert item["model"] == "models/gemini-embedding-001"
            assert item["taskType"] == TASK_DOCUMENT
            assert item["outputDimensionality"] == 768
    assert fake.sleeps == []


async def test_query_uses_query_task_type():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return ok_response(request)

    embedder, _ = build(handler)
    assert await embedder.embed_query("질문") == [2.0, 1.0]
    assert captured["body"]["requests"][0]["taskType"] == TASK_QUERY


async def test_retry_after_seconds_is_honored():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return ok_response(request)

    embedder, fake = build(handler)
    await embedder.embed_query("a")
    assert fake.sleeps == [7.0]


async def test_server_errors_back_off_then_give_up_as_retryable():
    embedder, fake = build(lambda request: httpx.Response(503))
    with pytest.raises(EmbeddingError) as error:
        await embedder.embed_query("a")
    assert error.value.retryable is True
    assert fake.sleeps == [1.0, 2.0, 4.0]


async def test_client_error_is_not_retryable_and_hides_key():
    embedder, fake = build(lambda request: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(EmbeddingError) as error:
        await embedder.embed_query("a")
    assert error.value.retryable is False
    assert fake.sleeps == []
    assert API_KEY not in str(error.value)


async def test_count_mismatch_is_not_retryable():
    embedder, _ = build(lambda request: httpx.Response(200, json={"embeddings": []}))
    with pytest.raises(EmbeddingError) as error:
        await embedder.embed_documents(["a", "b"])
    assert error.value.retryable is False


async def test_transport_error_is_retryable_without_leaking_url():
    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    embedder, fake = build(handler)
    with pytest.raises(EmbeddingError) as error:
        await embedder.embed_query("a")
    assert error.value.retryable is True
    assert API_KEY not in str(error.value)
    assert fake.sleeps == []


def test_retry_after_parsing():
    now = datetime(2015, 10, 21, 7, 28, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:10 GMT", now=now) == 10.0
    assert retry_after_seconds("Wed, 21 Oct 2015 07:27:00 GMT", now=now) is None
    assert retry_after_seconds("0") is None
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds(None) is None


async def test_token_bucket_waits_for_refill_in_slices():
    fake = FakeTime()
    bucket = TokenBucket(60, clock=fake.clock, sleep=fake.sleep)
    await bucket.acquire(60)
    assert fake.sleeps == []
    await bucket.acquire(30)
    assert sum(fake.sleeps) == pytest.approx(30.0)
    assert max(fake.sleeps) <= 5.0


async def test_token_bucket_does_not_hang_on_oversized_request():
    fake = FakeTime()
    bucket = TokenBucket(10, clock=fake.clock, sleep=fake.sleep)
    await bucket.acquire(1_000)  # 한도(10)보다 크다. 한도만큼만 기다리고 끝나야 한다
    assert fake.sleeps == []


def test_create_embedder_without_key_uses_local_fallback():
    assert isinstance(create_embedder(EmbeddingSettings(gemini_api_key="  ")), LocalHashingEmbedder)
    assert isinstance(create_embedder(EmbeddingSettings(gemini_api_key="k")), GeminiEmbedder)


async def test_local_embedder_matches_java_golden(golden_dir, load_json):
    cases = load_json(golden_dir / "expected" / "local-embeddings.json")
    embedder = LocalHashingEmbedder()
    assert cases
    for case in cases:
        vector = await embedder.embed_query(case["text"])
        assert len(vector) == 768
        actual = {str(i): v for i, v in enumerate(vector) if v != 0.0}
        assert actual.keys() == case["nonZero"].keys(), case["text"]
        for index, expected in case["nonZero"].items():
            assert actual[index] == pytest.approx(expected, abs=1e-6), (case["text"], index)


async def test_local_embedder_documents_and_query_share_vector_space():
    embedder = LocalHashingEmbedder()
    [document] = await embedder.embed_documents(["결제 정책"])
    assert document == await embedder.embed_query("결제 정책")

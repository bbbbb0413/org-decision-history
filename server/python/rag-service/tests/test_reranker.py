import asyncio
import httpx
import pytest
from history_shared.metrics import RERANK_FALLBACKS

from app.search.models import SearchHit
from app.search.reranker import CrossEncoderReranker, HttpRerankClient, RerankUnavailable


def make_hit(doc_id: int, chunk_no: int, content: str, score: float = 0.5) -> SearchHit:
    return SearchHit(
        document_id=doc_id,
        document_version=1,
        chunk_no=chunk_no,
        content=content,
        section_path="",
        chunk_tag="",
        score=score,
        vector_similarity=score,
    )


class FakeClient:
    """문단마다 점수를 정해 주는 재순위화 클라이언트."""

    def __init__(self, score_of=None, error: RerankUnavailable | None = None):
        self._score_of = score_of or (lambda text: 0.5)
        self._error = error
        self.calls: list[tuple[str, list[str]]] = []

    async def scores(self, query, texts):
        self.calls.append((query, texts))
        if self._error:
            raise self._error
        return [self._score_of(t) for t in texts]


def fallbacks(reason: str) -> float:
    return RERANK_FALLBACKS.labels(reason=reason)._value.get()


@pytest.mark.asyncio
async def test_reranker_disabled_fallback():
    before = fallbacks("disabled")
    reranker = CrossEncoderReranker(FakeClient(), enabled=False)
    hits = [
        make_hit(1, 0, "문서 A 내용", 0.9),
        make_hit(2, 0, "문서 B 내용", 0.8),
        make_hit(3, 0, "문서 C 내용", 0.7),
    ]
    results = await reranker.rerank("아무 질문", hits, top_k=2)
    assert [r.document_id for r in results] == [1, 2]
    assert fallbacks("disabled") == before + 1


@pytest.mark.asyncio
async def test_reranker_without_client_cannot_be_enabled():
    assert CrossEncoderReranker(None, enabled=True).enabled is False


@pytest.mark.asyncio
async def test_reranker_empty_inputs():
    reranker = CrossEncoderReranker(FakeClient())
    assert await reranker.rerank("", [make_hit(1, 0, "내용")]) == []
    assert await reranker.rerank("질문", []) == []


@pytest.mark.asyncio
async def test_reranker_mock_scoring():
    client = FakeClient(lambda text: 0.95 if ("재택" in text or "원격" in text) else 0.15)
    reranker = CrossEncoderReranker(client)

    hits = [
        make_hit(1, 0, "점심 식대는 12,000원까지 법인카드로 지원됩니다."),  # irrelevant
        make_hit(2, 0, "임직원은 주 2회 재택근무 및 원격 근무를 신청할 수 있습니다."),  # highly relevant
        make_hit(3, 0, "보안 규정상 원격 접속 시 OTP 인증이 필수입니다."),  # relevant
    ]

    results = await reranker.rerank("원격 근무 규정이 어떻게 되나요?", hits, top_k=2, min_score=0.3)

    assert {r.document_id for r in results} == {2, 3}
    assert results[0].rerank_score >= 0.9
    assert client.calls[0][1] == [h.content for h in hits]


@pytest.mark.asyncio
async def test_reranker_min_score_filter():
    reranker = CrossEncoderReranker(FakeClient(lambda text: 0.2))
    results = await reranker.rerank("질문", [make_hit(1, 0, "낮은 관련도 내용")], min_score=0.35)
    assert results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["timeout", "http_error", "bad_response"])
async def test_reranker_unavailable_falls_back_to_initial_order_and_counts(reason):
    before = fallbacks(reason)
    reranker = CrossEncoderReranker(FakeClient(error=RerankUnavailable(reason, "실패")))
    hits = [make_hit(1, 0, "문서 1", 0.9), make_hit(2, 0, "문서 2", 0.8)]

    # 예외를 던지지 않고 1차 검색 순서대로 돌려준다
    results = await reranker.rerank("질문", hits, top_k=1)

    assert [r.document_id for r in results] == [1]
    assert fallbacks(reason) == before + 1


def _reverse_client(n: int) -> FakeClient:
    """1차 순위와 정반대로 점수를 준다(마지막 후보가 최고점)."""
    order = {f"문서 {i}": i for i in range(1, n + 1)}
    return FakeClient(lambda text: order[text] / n)


@pytest.mark.asyncio
async def test_reranker_weight_decides_between_rerank_and_initial_order():
    """리랭커 비중(설정 rerank_weight)이 최종 순위를 정한다. 1 이면 리랭커 순, 0 이면 1차 검색 순."""
    hits = [make_hit(i, 0, f"문서 {i}") for i in range(1, 5)]

    only_rerank = await CrossEncoderReranker(_reverse_client(4), weight=1.0).rerank("질문", hits, top_k=4)
    only_initial = await CrossEncoderReranker(_reverse_client(4), weight=0.0).rerank("질문", hits, top_k=4)

    assert [h.document_id for h in only_rerank] == [4, 3, 2, 1]
    assert [h.document_id for h in only_initial] == [1, 2, 3, 4]



def _scores_client(scores: list[float]) -> FakeClient:
    by_text = {f"문서 {i}": s for i, s in enumerate(scores, start=1)}
    return FakeClient(lambda text: by_text[text])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scores, rerank_wins",
    [
        # 확신이 낮을 뿐 순서는 또렷하다(bge-reranker-base 의 실제 분포) — 로짓 폭이 넓어 리랭커 비중을 그대로 쓴다
        ([0.00001, 0.00002, 0.00001, 0.006], True),
        # 모든 문단이 0.999 근처로 포화했다(옛 다국어 모델의 한국어 분포) — 1차 검색 순위를 더 믿는다
        ([0.9995, 0.9993, 0.9994, 0.9997], False),
    ],
)
async def test_saturation_is_judged_on_logit_spread(scores, rerank_wins):
    hits = [make_hit(i, 0, f"문서 {i}") for i in range(1, 5)]
    results = await CrossEncoderReranker(_scores_client(scores)).rerank("질문", hits, top_k=4)
    assert (results[0].document_id == 4) is rerank_wins

# ─── reranker-service(TEI) /rerank 호출 ────────────

def _http_client(handler) -> HttpRerankClient:
    return HttpRerankClient("http://reranker:8090/", api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_http_client_scores_one_text_per_request_in_order():
    """묶어 보내면 int8 모델 점수가 함께 든 후보에 따라 바뀐다 — 한 건씩 보낸다."""
    import json
    seen = []
    by_text = {"가": 0.5, "나": 0.1, "다": 0.9}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((str(request.url), request.headers.get("Authorization"), body))
        return httpx.Response(200, json=[{"index": 0, "score": by_text[body["texts"][0]]}])

    scores = await _http_client(handle).scores("질문", ["가", "나", "다"])

    assert scores == [0.5, 0.1, 0.9]
    assert seen == [("http://reranker:8090/rerank", "Bearer k", {"query": "질문", "texts": [t], "truncate": True}) for t in "가나다"]


@pytest.mark.asyncio
async def test_concurrent_questions_do_not_share_a_server_batch():
    """동시에 들어온 두 질문의 요청이 서버에 겹쳐 있지 않다(겹치면 TEI 가 한 묶음으로 계산한다)."""
    in_flight, peak = 0, 0

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return httpx.Response(200, json=[{"index": 0, "score": 0.5}])

    client = HttpRerankClient("http://reranker:8090", client=httpx.AsyncClient(transport=Transport()))
    await asyncio.gather(client.scores("질문1", ["가", "나"]), client.scores("질문2", ["다", "라"]))
    assert peak == 1


@pytest.mark.asyncio
async def test_total_time_limit_includes_waiting_for_other_questions():
    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(0.05)
            return httpx.Response(200, json=[{"index": 0, "score": 0.5}])

    client = HttpRerankClient("http://reranker:8090", timeout_seconds=0.12, client=httpx.AsyncClient(transport=Slow()))
    with pytest.raises(RerankUnavailable) as error:
        await client.scores("질문", ["가", "나", "다", "라"])
    assert error.value.reason == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, reason",
    [
        (httpx.Response(503, json={"code": "x"}), "http_error"),
        (httpx.Response(200, content=b"not json"), "bad_response"),
        (httpx.Response(200, json={"index": 0}), "bad_response"),
        (httpx.Response(200, json=[]), "bad_response"),  # 문단의 점수가 빠짐
    ],
)
async def test_http_client_classifies_failures(response, reason):
    with pytest.raises(RerankUnavailable) as error:
        await _http_client(lambda request: response).scores("질문", ["가", "나"])
    assert error.value.reason == reason


@pytest.mark.asyncio
async def test_http_client_timeout_is_classified():
    def handle(request):
        raise httpx.ReadTimeout("느림", request=request)

    with pytest.raises(RerankUnavailable) as error:
        await _http_client(handle).scores("질문", ["가"])
    assert error.value.reason == "timeout"


@pytest.mark.asyncio
async def test_rerank_each_gives_every_sub_query_a_top_slot():
    """두 대상을 묻는 질문: 합친 질문 하나로 매기면 한 대상이 근거를 다 차지한다. 하위 질의마다 매겨 번갈아 채운다."""
    hits = [make_hit(1, i, f"로그 {i}") for i in range(4)] + [make_hit(2, i, f"반출 {i}") for i in range(4)]

    def score_of_query(query):
        topic = "로그" if "로그" in query else "반출"
        return lambda text: 0.9 if text.startswith(topic) else 0.001

    class PerQueryClient:
        async def scores(self, query, texts):
            return [score_of_query(query)(t) for t in texts]

    reranker = CrossEncoderReranker(PerQueryClient())
    merged = await reranker.rerank_each(["로그 보관 기간", "반출 승인권자"], hits, top_k=3)

    assert [h.content.split()[0] for h in merged[:2]] == ["로그", "반출"]
    assert len(merged) == 3 and len({h.key for h in merged}) == 3


@pytest.mark.asyncio
async def test_rerank_node_splits_slots_only_for_complex_plans():
    from unittest.mock import MagicMock

    from app.graph.nodes import GraphNodes

    client = FakeClient()
    nodes = GraphNodes(
        db=MagicMock(), auth=MagicMock(), hybrid_search=MagicMock(), source_resolver=MagicMock(), answer_cache=MagicMock(),
        memory=MagicMock(), generator=MagicMock(), reranker=CrossEncoderReranker(client),
    )
    hits = [make_hit(1, i, f"문단 {i}") for i in range(3)]
    base = {"rewritten_question": "로그와 반출", "hits": hits}

    await nodes.rerank({**base, "complexity": "complex", "search_queries": ["로그", "반출"]})
    assert [q for q, _ in client.calls] == ["로그", "반출"]

    client.calls.clear()
    # abstract 의 두 번째 질의는 가상 문서라 재순위화 질의로 쓰지 않는다
    await nodes.rerank({**base, "complexity": "abstract", "search_queries": ["로그와 반출", "가상 문서"]})
    assert [q for q, _ in client.calls] == ["로그와 반출"]

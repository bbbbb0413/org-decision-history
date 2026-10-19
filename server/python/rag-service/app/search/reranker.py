"""2차 재순위화(Rerank).

1차 하이브리드 검색(Qdrant Dense + Postgres Sparse RRF)으로 모은 후보 청크를 크로스 인코더 점수로 다시 매긴다.
추론은 별도 서비스(reranker-service)가 한다 — 이 모듈은 점수를 받아 1차 순위와 섞어 상위 Top-K 를 고른다.
reranker-service 는 Hugging Face TEI 이미지다(server/inference/reranker). 모델을 바꿔도 여기는 그대로다.

장애 격리: 재순위화 서비스가 꺼져 있거나 느리거나 이상한 응답을 주면 1차 검색 순서로 답한다.
그 횟수를 rerank_fallback_total{reason} 으로 남긴다 — 0 이 아니면 답변 품질이 떨어진 것이다.
"""
import asyncio
import math
from typing import List, Protocol

import httpx
import structlog
from history_shared.metrics import RERANK_FALLBACKS

from app.search.models import SearchHit

log = structlog.get_logger()


class RerankUnavailable(Exception):
    """재순위화 점수를 얻지 못했다. reason 은 지표 라벨이다(timeout / http_error / bad_response)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class RerankClient(Protocol):
    async def scores(self, query: str, texts: list[str]) -> list[float]:
        """texts 와 같은 순서로 0~1 관련도를 돌려준다. 얻지 못하면 RerankUnavailable."""
        ...


class HttpRerankClient:
    """reranker-service(TEI) 의 POST /rerank 를 부른다. 키는 TEI --api-key 방식대로 Bearer 토큰으로 보낸다.

    후보를 **한 건씩, 한 번에 하나만** 보낸다. int8 동적 양자화 모델은 활성값 스케일을 묶음 전체로 정해서, 여러 후보를
    한 묶음으로 계산하면 한 후보의 점수가 함께 든 후보·패딩에 따라 바뀐다(같은 요청을 반복해도 로짓이 최대 2 넘게 흔들렸다).
    TEI ONNX 백엔드는 서버 설정과 상관없이 최대 8건씩 묶으므로(max_batch_requests 를 8로 강제한다) 서버에서는 막을 수 없다.
    잠금은 동시에 들어온 다른 질문의 후보가 서버에서 같은 묶음에 섞이는 것을 막는다(인스턴스가 하나일 때). CPU 1개에서는
    어차피 순서대로 계산되므로 처리량은 그대로이고, 패딩이 없어 후보 35건 기준으로 묶어 보낼 때보다 빨랐다(3.0초 → 2.3초).
    """

    def __init__(self, base_url: str, *, api_key: str = "", timeout_seconds: float = 8.0, client: httpx.AsyncClient | None = None):
        self._url = base_url.rstrip("/") + "/rerank"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        # 잠금 대기까지 포함한 한 질문의 재순위화 시간 상한. 넘으면 1차 검색 순서로 답한다
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def scores(self, query: str, texts: list[str]) -> list[float]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._lock:
                    return [await self._score_one(query, text) for text in texts]
        except TimeoutError as e:
            raise RerankUnavailable("timeout", f"재순위화 응답 시간 초과({self._timeout_seconds}초)") from e

    async def _score_one(self, query: str, text: str) -> float:
        try:
            response = await self._client.post(
                self._url, json={"query": query, "texts": [text], "truncate": True}, headers=self._headers
            )
            response.raise_for_status()
            ranks = response.json()
        except httpx.TimeoutException as e:
            raise RerankUnavailable("timeout", f"재순위화 응답 시간 초과: {e}") from e
        except httpx.HTTPError as e:
            raise RerankUnavailable("http_error", f"재순위화 호출 실패: {e}") from e
        except ValueError as e:
            raise RerankUnavailable("bad_response", f"재순위화 응답을 읽을 수 없다: {e}") from e
        return _scores_in_order(ranks, 1)[0]


def _scores_in_order(ranks, count: int) -> list[float]:
    """[{index, score}] (점수순) 를 texts 순서의 점수 목록으로 되돌린다. 모든 문단의 점수가 있어야 한다."""
    if not isinstance(ranks, list):
        raise RerankUnavailable("bad_response", "재순위화 응답이 목록이 아니다")
    scores: list[float | None] = [None] * count
    try:
        for r in ranks:
            scores[int(r["index"])] = float(r["score"])
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise RerankUnavailable("bad_response", f"재순위화 응답 항목이 계약과 다르다: {e}") from e
    if any(s is None for s in scores):
        raise RerankUnavailable("bad_response", "재순위화 응답에 점수가 빠진 문단이 있다")
    return scores  # type: ignore[return-value]


def _logit(p: float) -> float:
    p = min(max(p, 1e-7), 1 - 1e-7)
    return math.log(p / (1 - p))


class CrossEncoderReranker:
    def __init__(
        self,
        client: RerankClient | None = None,
        *,
        enabled: bool = True,
        weight: float = 0.7,
        saturated_weight: float = 0.3,
        saturation_spread: float = 1.0,
    ):
        self._client = client
        # 클라이언트가 없으면 켤 수 없다(테스트·설정 오류). 1차 검색 순서로 답한다
        self.enabled = enabled and client is not None
        # 최종 순위에서 리랭커 점수가 차지하는 비중. 나머지는 1차 검색 순위가 차지한다
        self._weight = weight
        self._saturated_weight = saturated_weight
        self._saturation_spread = saturation_spread

    async def rerank(
        self,
        query: str,
        hits: List[SearchHit],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[SearchHit]:
        """후보 청크를 재순위화 점수로 다시 매기고 상위 top_k 건을 돌려준다."""
        if not hits or not query.strip():
            return []

        if not self.enabled:
            RERANK_FALLBACKS.labels(reason="disabled").inc()
            return hits[:top_k]

        try:
            scores = await self._client.scores(query, [hit.content or "" for hit in hits])
        except RerankUnavailable as e:
            RERANK_FALLBACKS.labels(reason=e.reason).inc()
            log.warning("재순위화 실패, 1차 검색 순서로 답한다", reason=e.reason, error=str(e))
            return hits[:top_k]

        return self._blend(hits, scores, top_k, min_score)

    async def rerank_each(
        self,
        queries: list[str],
        hits: List[SearchHit],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[SearchHit]:
        """여러 대상을 묻는 질문(질의 계획 complex)용. 하위 질의마다 따로 재순위화하고 근거 자리를 번갈아 채운다.

        합쳐진 질문 하나로 매기면 점수가 한 대상에 몰려 다른 대상의 청크가 근거 밖으로 밀린다. 번갈아 채우면
        상위 몇 건(판정기가 보는 범위)에 대상마다 1위 청크가 든다. 리랭크 호출은 하위 질의 수만큼 늘어난다.
        """
        ranked = [await self.rerank(q, hits, top_k, min_score) for q in queries if q.strip()]
        merged: List[SearchHit] = []
        seen: set[str] = set()
        for rank in range(top_k):
            for per_query in ranked:
                if rank < len(per_query) and per_query[rank].key not in seen and len(merged) < top_k:
                    seen.add(per_query[rank].key)
                    merged.append(per_query[rank])
        return merged

    def _blend(self, hits: List[SearchHit], scores: list[float], top_k: int, min_score: float) -> List[SearchHit]:
        # 리랭커 점수 분포와 변별력(spread)
        min_s, max_s = min(scores), max(scores)
        spread = max_s - min_s
        # 크로스 인코더 점수 폭이 좁으면(포화) 후보를 가리지 못한 것이다. 1차 하이브리드 순위를 더 믿는다.
        # 폭은 시그모이드를 푼 로짓으로 잰다 — 확률로 재면 "모두 0.999"(포화)와 "0.006 대 0.00001"(관련 없는 질문이
        # 아니라 확신이 낮을 뿐 순서는 또렷하다)이 둘 다 0.01 미만이라 구분되지 않는다
        w = self._saturated_weight if _logit(max_s) - _logit(min_s) < self._saturation_spread else self._weight

        scored_hits: List[tuple[float, SearchHit]] = []
        for idx, (hit, raw_score) in enumerate(zip(hits, scores, strict=True)):
            if raw_score < min_score:
                continue
            # 1차 하이브리드 검색 순위(Rank) 기반 감쇄 점수
            initial_rank_score = 1.0 / (idx + 1)
            norm_rerank = (raw_score - min_s) / spread if spread > 0.0001 else 0.5
            blended = (1 - w) * initial_rank_score + w * norm_rerank
            scored_hits.append((blended, hit.with_rerank(raw_score)))

        scored_hits.sort(key=lambda x: x[0], reverse=True)
        return [hit for _, hit in scored_hits[:top_k]]

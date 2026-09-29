"""Cross-Encoder 2차 재순위화(Reranker) 모듈.

1차 하이브리드 검색(Qdrant Dense + Postgres Sparse RRF)으로 수집된 후보군 청크들에 대해,
질문과 문서 청크 간 심층 교차 어텐션(Cross-Attention) 점수를 산출하여
가장 관련성이 높은 상위 Top-K 청크를 정밀 선별한다.

- 엔진: FlashRank (ONNX Runtime 기반 초경량 크로스 인코더, PyTorch 불필요)
- 기본 모델: ms-marco-MultiBERT-L-12 (다국어/한국어 지원)
- 비동기 처리: asyncio.to_thread 로 메인 이벤트 루프 블로킹 방지
- 장애 격리: 모델 로드 실패 또는 런타임 오류 시 원본 하이브리드 순서로 자동 폴백(Graceful Fallback)
"""
import asyncio
from typing import List, Optional
import structlog

from app.search.models import SearchHit

log = structlog.get_logger()


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "ms-marco-MultiBERT-L-12",
        cache_dir: Optional[str] = "/tmp",
        enabled: bool = True,
    ):
        self.model_name = model_name
        self.enabled = enabled
        self._ranker = None
        self._init_failed = False
        if enabled:
            self._init_model(model_name, cache_dir)

    def _init_model(self, model_name: str, cache_dir: Optional[str]) -> None:
        try:
            from flashrank import Ranker
            self._ranker = Ranker(model_name=model_name, cache_dir=cache_dir)
            log.info("CrossEncoderReranker 모델 로드 성공", model=model_name)
        except Exception as e:
            self._init_failed = True
            log.warning("CrossEncoderReranker 모델 로드 실패 (Fallback 모드로 동작)", error=str(e), model=model_name)

    async def rerank(
        self,
        query: str,
        hits: List[SearchHit],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[SearchHit]:
        """후보 청크 리스트를 교차 어텐션 점수로 재순위화하고 상위 top_k 건을 반환한다."""
        if not hits or not query.strip():
            return []

        # 리랭커가 비활성화되었거나 미로드된 경우 원본 검색 결과 반환
        if not self.enabled or self._ranker is None:
            return hits[:top_k]

        def _do_rerank() -> List[SearchHit]:
            from flashrank import RerankRequest
            passages = [
                {"id": i, "text": hit.content or ""}
                for i, hit in enumerate(hits)
            ]
            req = RerankRequest(query=query, passages=passages)
            results = self._ranker.rerank(req)

            # Reranker 점수 분포 및 변별력(spread) 분석
            scores = [float(r["score"]) for r in results]
            min_s, max_s = min(scores), max(scores)
            spread = max_s - min_s

            scored_hits: List[tuple[float, SearchHit]] = []
            for r in results:
                idx = int(r["id"])
                raw_score = float(r["score"])
                if raw_score < min_score:
                    continue

                # 1차 하이브리드 검색 순위(Rank) 기반 감쇄 점수
                initial_rank_score = 1.0 / (idx + 1)

                # 점수 정규화
                if spread > 0.0001:
                    norm_rerank = (raw_score - min_s) / spread
                else:
                    norm_rerank = 0.5

                # 크로스 인코더 로짓 포화(spread < 0.01) 시 1차 하이브리드 순위를 더 신뢰
                if spread < 0.01:
                    blended = 0.7 * initial_rank_score + 0.3 * norm_rerank
                else:
                    blended = 0.3 * initial_rank_score + 0.7 * norm_rerank

                scored_hits.append((blended, hits[idx].with_rerank(raw_score)))

            scored_hits.sort(key=lambda x: x[0], reverse=True)
            return [hit for _, hit in scored_hits[:top_k]]

        try:
            # CPU 바운드 연산이므로 별도 작업자 스레드풀에서 실행
            return await asyncio.to_thread(_do_rerank)
        except Exception as e:
            log.error("CrossEncoderReranker 추론 중 오류 발생, 1차 검색 순서로 폴백", error=str(e))
            return hits[:top_k]

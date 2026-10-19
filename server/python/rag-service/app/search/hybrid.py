"""벡터 검색과 키워드 검색을 병렬로 돌리고 LangChain EnsembleRetriever로 RRF 병합한다.

두 검색을 순서대로 하면 지연이 더해진다. 서로
독립이므로 함께 보낸다. 임베딩이 실패하면 키워드 검색만으로 진행한다.

RRF 융합은 직접 구현하지 않고 `EnsembleRetriever.weighted_reciprocal_rank`를 그대로
쓴다 — `id_key="chunk_key"`로 dedup 키를, `c=60`으로 K 상수를, `weights=[1.0, 1.0]`로
무가중치 합산을 명시해 라이브러리 기본값에 기대는 모호함을 없앴다.
"""
import asyncio

import structlog
from history_shared.embeddings import Embedder
from langchain.retrievers.ensemble import EnsembleRetriever
from langchain_core.documents import Document

from app.auth_client import PermissionScope
from app.search.keyword_retriever import KeywordRetriever, KeywordSearchClient
from app.search.models import SearchHit
from app.search.vector_retriever import VectorRetriever, VectorSearchClient

log = structlog.get_logger()



class HybridSearchService:
    def __init__(
        self,
        embedder: Embedder,
        vector_client: VectorSearchClient,
        keyword_client: KeywordSearchClient,
        candidates: int = 20,
        min_vector_similarity: float = 0.5,
    ):
        self._embedder = embedder
        self._vector_client = vector_client
        self._keyword_client = keyword_client
        # 각 검색에서 가져올 후보 수. 병합 후 상위 limit개만 쓰지만 후보는 넉넉히 본다.
        # limit 이 이보다 크면 limit 만큼 가져온다 — 호출부가 요청한 수가 조용히 잘리지 않게
        self._candidates = candidates
        # 벡터 검색에서만 걸리고(키워드 일치가 전혀 없고) 코사인 유사도가 이 미만이면 버린다.
        self._min_vector_similarity = min_vector_similarity

    async def search(self, query: str, scope: PermissionScope, limit: int) -> list[SearchHit]:
        return await self.search_multi([query], scope, limit)

    async def search_multi(self, queries: list[str], scope: PermissionScope, limit: int) -> list[SearchHit]:
        valid_queries = [q.strip() for q in queries if q.strip()]
        if scope.is_empty() or not valid_queries:
            return []

        candidates = max(self._candidates, limit)
        vector_retriever = VectorRetriever(
            client=self._vector_client, embedder=self._embedder, scope=scope, limit=candidates
        )
        keyword_retriever = KeywordRetriever(client=self._keyword_client, scope=scope, limit=candidates)

        tasks = []
        for q in valid_queries:
            tasks.append(vector_retriever.ainvoke(q))
            tasks.append(keyword_retriever.ainvoke(q))

        results = await asyncio.gather(*tasks)

        all_doc_lists = list(results)
        keyword_matched = set()
        for i in range(1, len(results), 2):
            keyword_matched.update(self._to_hit(d).key for d in results[i])

        weights = [1.0] * len(all_doc_lists)
        ensemble = EnsembleRetriever(
            retrievers=[vector_retriever] * len(all_doc_lists),
            weights=weights,
            id_key="chunk_key",
            c=60,
        )
        fused_docs = ensemble.weighted_reciprocal_rank(all_doc_lists)[:candidates]
        fused_hits = [self._to_hit(d) for d in fused_docs]
        filled = await self._keyword_client.fill_content(fused_hits)

        relevant = [h for h in filled if self._is_relevant(h, keyword_matched)]
        return relevant[:limit]

    def _to_hit(self, doc: Document) -> SearchHit:
        m = doc.metadata
        return SearchHit(
            document_id=m["document_id"],
            document_version=m["document_version"],
            chunk_no=m["chunk_no"],
            content=doc.page_content or None,
            section_path=m.get("section_path"),
            chunk_tag=m.get("chunk_tag"),
            score=0.0,
            vector_similarity=m.get("vector_similarity"),
        )

    def _is_relevant(self, hit: SearchHit, keyword_matched: set[str]) -> bool:
        if hit.key in keyword_matched:
            return True
        sim = hit.vector_similarity
        return sim is not None and sim >= self._min_vector_similarity

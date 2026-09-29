"""Qdrant 벡터 검색 클라이언트. Qdrant 접근은 history_shared.vector 를 쓴다.

- 같은 조직 문서끼리만 비교한다. scope_key 는 팀이 있으면 team:{id} 라 조직 전체를 거를 수 없어
  payload 의 organization_id 로 거른다.
- Qdrant payload 에는 청크 본문이 없다. ChunkContentReader 가 indexing.document_chunk 에서 채운다.
"""
from typing import List, Optional

import structlog
from history_shared.vector import ChunkVectorStore
from qdrant_client import models

from app.chunk_reader import ChunkContentReader
from app.models import SearchHit
from app.settings import Settings

log = structlog.get_logger()


class VectorSearchClient:
    def __init__(self, settings: Settings, chunks: ChunkContentReader, store: ChunkVectorStore | None = None):
        self._store = store or ChunkVectorStore(settings)
        self._chunks = chunks

    async def close(self) -> None:
        await self._store.close()

    async def search_points(
        self,
        vector: list[float],
        organization_id: int,
        exclude_doc_id: Optional[int] = None,
        limit: int = 5,
    ) -> List[SearchHit]:
        query_filter = models.Filter(
            must=[models.FieldCondition(key="organization_id", match=models.MatchValue(value=organization_id))]
        )
        try:
            # 자기 문서를 빼고도 limit 건이 남도록 조금 더 가져온다
            points = await self._store.search(vector, query_filter=query_filter, limit=limit + 2, exact=False)
        except Exception as e:  # noqa: BLE001 — 검색이 실패하면 이 조항의 비교를 건너뛴다
            log.warning("상충 후보 벡터 검색 실패", error=str(e))
            return []

        candidates = []
        for point in points:
            payload = point.payload or {}
            doc_id = payload.get("document_id")
            if exclude_doc_id and doc_id == exclude_doc_id:
                continue
            candidates.append((point, payload))
            if len(candidates) >= limit:
                break

        contents = await self._chunks.content_of(
            [(p.get("document_id"), p.get("document_version", 1), p.get("chunk_no", 0)) for _, p in candidates]
        )

        hits: list[SearchHit] = []
        for point, payload in candidates:
            ref = (payload.get("document_id"), payload.get("document_version", 1), payload.get("chunk_no", 0))
            score = float(point.score)
            hits.append(
                SearchHit(
                    document_id=payload.get("document_id"),
                    document_version=payload.get("document_version", 1),
                    chunk_no=payload.get("chunk_no", 0),
                    content=contents.get(ref),
                    section_path=payload.get("section_path"),
                    chunk_tag=payload.get("chunk_tag"),
                    score=score,
                    vector_similarity=score,
                )
            )
        return hits

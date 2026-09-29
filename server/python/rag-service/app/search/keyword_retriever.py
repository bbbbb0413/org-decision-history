"""PostgreSQL 전문검색. LangChain BaseRetriever 어댑터를 겸한다.

임베딩은 뜻이 가까운 것을 찾지 정확히 같은 낱말을 찾지 않는다. 'ADR-014'나
'게이트웨이 라우팅' 같은 고유명사는 이쪽이 잡는다.
"""
import re
from typing import Any

import asyncpg
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from app.auth_client import PermissionScope
from app.search.models import SearchHit
from app.search.vector_retriever import chunk_key

_TERM_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)


def _to_tsquery(query: str) -> str:
    """사용자 질문을 tsquery로 바꾼다.

    낱말을 OR로 잇는다. AND로 묶으면 한 낱말만 없어도 결과가 0건이 되어,
    한국어처럼 조사가 붙는 언어에서 지나치게 자주 빈손이 된다.
    """
    cleaned = _TERM_PATTERN.sub(" ", query).strip()
    terms = [t for t in cleaned.split() if len(t) >= 2]
    if not terms:
        return re.sub(r"[^\w]", "", query, flags=re.UNICODE)
    return " | ".join(f"{t}:*" for t in terms)


class KeywordSearchClient:
    """Postgres 전문검색을 직접 보내는 저수준 클라이언트."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def search(self, query: str, scope: PermissionScope, limit: int) -> list[SearchHit]:
        if scope.is_empty() or not query or not query.strip():
            return []

        where = ["c.search_vector @@ q.tsq"]
        params: list = [_to_tsquery(query)]

        if not scope.system_admin:
            where.append(f"c.scope_key = ANY(${len(params) + 1})")
            params.append(scope.to_scope_keys())
        if scope.denied_document_ids:
            where.append(f"c.document_id != ALL(${len(params) + 1})")
            params.append(list(scope.denied_document_ids))

        params.append(limit)
        sql = f"""
            SELECT c.document_id, c.document_version, c.chunk_no, c.content,
                   c.section_path, c.chunk_tag::text AS chunk_tag,
                   ts_rank(c.search_vector, q.tsq) AS score
            FROM indexing.document_chunk c,
                 to_tsquery('simple', $1) AS q(tsq)
            WHERE {' AND '.join(where)}
            ORDER BY score DESC
            LIMIT ${len(params)}
        """
        rows = await self._pool.fetch(sql, *params)
        return [
            SearchHit(
                document_id=r["document_id"],
                document_version=r["document_version"],
                chunk_no=r["chunk_no"],
                content=r["content"],
                section_path=r["section_path"],
                chunk_tag=r["chunk_tag"],
                score=float(r["score"]),
                # 전문검색 순위 점수는 코사인 유사도와 단위가 달라 vector_similarity로 쓸 수 없다.
                vector_similarity=None,
            )
            for r in rows
        ]

    async def fill_content(self, hits: list[SearchHit]) -> list[SearchHit]:
        """청크 본문을 채운다. 벡터 검색 결과에는 본문이 없다."""
        need_content = [h for h in hits if h.content is None]
        if not need_content:
            return hits

        loaded: dict[str, SearchHit] = {}
        for hit in need_content:
            row = await self._pool.fetchrow(
                """
                SELECT content, section_path, chunk_tag::text AS chunk_tag
                FROM indexing.document_chunk
                WHERE document_id = $1 AND document_version = $2 AND chunk_no = $3
                """,
                hit.document_id,
                hit.document_version,
                hit.chunk_no,
            )
            if row is not None:
                loaded[hit.key] = SearchHit(
                    document_id=hit.document_id,
                    document_version=hit.document_version,
                    chunk_no=hit.chunk_no,
                    content=row["content"],
                    section_path=row["section_path"],
                    chunk_tag=row["chunk_tag"],
                    score=hit.score,
                    vector_similarity=hit.vector_similarity,
                )

        # 본문을 찾지 못한 청크는 버린다. 색인이 지워졌는데 벡터만 남은 경우다.
        filled = []
        for h in hits:
            resolved = h if h.content is not None else loaded.get(h.key)
            if resolved is not None:
                filled.append(resolved)
        return filled


class KeywordRetriever(BaseRetriever):
    """EnsembleRetriever에 꽂는 키워드 검색 어댑터. 요청(scope)마다 새로 만든다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: KeywordSearchClient
    scope: PermissionScope
    limit: int = 20

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        raise NotImplementedError("KeywordRetriever는 비동기 전용이다 — ainvoke()를 쓴다.")

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun, **kwargs: Any
    ) -> list[Document]:
        hits = await self.client.search(query, self.scope, self.limit)
        return [
            Document(
                page_content=hit.content or "",
                metadata={
                    "chunk_key": chunk_key(hit.document_id, hit.document_version, hit.chunk_no),
                    "document_id": hit.document_id,
                    "document_version": hit.document_version,
                    "chunk_no": hit.chunk_no,
                    "section_path": hit.section_path,
                    "chunk_tag": hit.chunk_tag,
                    "vector_similarity": None,
                },
            )
            for hit in hits
        ]

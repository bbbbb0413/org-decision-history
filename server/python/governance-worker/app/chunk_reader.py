"""Qdrant 검색 결과의 본문을 indexing.document_chunk에서 읽는다.

Qdrant payload에는 청크 본문이 없다(qdrant-chunk-payload.schema.json). index-worker가 전문검색용으로
저장해 둔 indexing.document_chunk가 진짜 본문을 갖고 있으므로, 검색 결과의 (document_id, document_version,
chunk_no)로 그 행을 찾아 본문을 채운다.
"""
from collections.abc import Sequence

import asyncpg

_SELECT = """
    SELECT document_id, document_version, chunk_no, content
    FROM indexing.document_chunk
    WHERE (document_id, document_version, chunk_no) IN (
        SELECT * FROM unnest($1::bigint[], $2::int[], $3::int[])
    )
"""


class ChunkContentReader:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def content_of(self, refs: Sequence[tuple[int, int, int]]) -> dict[tuple[int, int, int], str]:
        """(document_id, document_version, chunk_no) 조합별 본문. 한 번에 조회해 후보마다 왕복하지 않는다."""
        if not refs:
            return {}
        doc_ids, versions, chunk_nos = zip(*refs, strict=True)
        rows = await self._pool.fetch(_SELECT, list(doc_ids), list(versions), list(chunk_nos))
        return {(r["document_id"], r["document_version"], r["chunk_no"]): r["content"] for r in rows}

"""청크를 PostgreSQL 전문검색 색인(indexing.document_chunk)에 저장한다.

벡터만으로는 고유명사를 놓친다. 두 색인을 함께 두고 검색 단계에서 병합한다.
한국어 형태소 분석기가 기본 배포에 없어 simple 설정을 쓴다.
"""
import json
from collections.abc import Sequence

import asyncpg

from app.chunk import Chunk
from app.documents import DocumentSource

_UPSERT = """
    INSERT INTO indexing.document_chunk
        (document_id, document_version, chunk_no, content, section_path, chunk_tag,
         token_count, scope_key, search_vector, vector_point_id, page_start, page_end, bboxes)
    VALUES ($1, $2, $3, $4, $5, $6::indexing.chunk_tag, $7, $8, to_tsvector('simple', $4), $9::uuid,
            $10, $11, $12::jsonb)
    ON CONFLICT (document_id, document_version, chunk_no) DO UPDATE SET
        content = EXCLUDED.content,
        section_path = EXCLUDED.section_path,
        chunk_tag = EXCLUDED.chunk_tag,
        token_count = EXCLUDED.token_count,
        scope_key = EXCLUDED.scope_key,
        search_vector = EXCLUDED.search_vector,
        vector_point_id = EXCLUDED.vector_point_id,
        page_start = EXCLUDED.page_start,
        page_end = EXCLUDED.page_end,
        bboxes = EXCLUDED.bboxes
"""


class ChunkStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def replace(self, source: DocumentSource, chunks: Sequence[Chunk], point_ids: Sequence[str]) -> None:
        """현재 버전 청크를 저장하고 같은 문서의 다른 버전 청크를 지운다.

        두 작업을 한 트랜잭션으로 묶는다. 따로 커밋하면 구버전과 신버전이 함께 검색되는 순간이 생긴다.
        """
        rows = [
            (
                source.id,
                source.version,
                chunk.chunk_no,
                chunk.content,
                chunk.section_path,
                chunk.tag,
                chunk.token_count,
                source.scope_key,
                point_id,
                chunk.page_start,
                chunk.page_end,
                json.dumps(chunk.bboxes) if chunk.bboxes is not None else None,
            )
            for chunk, point_id in zip(chunks, point_ids, strict=True)
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.executemany(_UPSERT, rows)
            await conn.execute(
                "DELETE FROM indexing.document_chunk WHERE document_id = $1 AND document_version <> $2",
                source.id,
                source.version,
            )

    async def remove_all_of(self, document_id: int) -> int:
        status = await self._pool.execute("DELETE FROM indexing.document_chunk WHERE document_id = $1", document_id)
        return int(status.split()[-1])

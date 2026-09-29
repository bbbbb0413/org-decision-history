"""Qdrant 검색 결과의 (document_id, document_version, chunk_no)로 indexing.document_chunk 본문을 읽는다."""
from app.chunk_reader import ChunkContentReader


async def test_content_of_batches_lookups_by_ref(pool, add_document, add_chunk):
    doc = await add_document()
    await add_chunk(document_id=doc, content="첫 번째 청크", chunk_no=0)
    await add_chunk(document_id=doc, content="두 번째 청크", chunk_no=1)
    reader = ChunkContentReader(pool)

    result = await reader.content_of([(doc, 1, 0), (doc, 1, 1), (doc, 1, 99)])

    assert result[(doc, 1, 0)] == "첫 번째 청크"
    assert result[(doc, 1, 1)] == "두 번째 청크"
    assert (doc, 1, 99) not in result  # 없는 조합은 그냥 빠진다


async def test_content_of_empty_refs_returns_empty_without_querying(pool):
    assert await ChunkContentReader(pool).content_of([]) == {}

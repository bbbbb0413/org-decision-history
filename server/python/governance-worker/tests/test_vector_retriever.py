"""VectorSearchClient. 조직 필터와 청크 본문 채움을 확인한다.

Qdrant는 메모리 클라이언트로 돌리고, 본문은 실제 PostgreSQL의 indexing.document_chunk에서 읽는다.
"""
import pytest
from history_shared.config import QdrantSettings
from history_shared.vector import ChunkPoint, ChunkVectorStore
from qdrant_client import AsyncQdrantClient

from app.chunk_reader import ChunkContentReader
from app.vector_retriever import VectorSearchClient


def _point(doc_id: int, org_id: int, vector: list[float]) -> ChunkPoint:
    return ChunkPoint(
        id=f"00000000-0000-0000-0000-{doc_id:012d}",
        vector=vector,
        payload={
            "document_id": doc_id,
            "document_version": 1,
            "chunk_no": 0,
            "scope_key": f"org:{org_id}",
            "chunk_tag": "DECISION",
            "document_type": "POLICY_DOC",
            "authored_at": "2024-01-01",
            "model_name": "test",
            "organization_id": org_id,
            "dim": 4,
        },
    )


@pytest.fixture
async def qdrant():
    client = AsyncQdrantClient(location=":memory:")
    yield client
    await client.close()


async def test_search_excludes_other_organizations_and_fills_content(pool, add_document, add_chunk, qdrant):
    store = ChunkVectorStore(QdrantSettings(qdrant_collection="test_chunks"), client=qdrant, dimension=4)
    await store.ensure_collection()
    own_doc = await add_document(organization_id=1)
    other_doc = await add_document(organization_id=2)
    await add_chunk(document_id=own_doc, content="같은 조직 청크 본문")
    await store.upsert(
        [
            _point(own_doc, 1, [1.0, 0.0, 0.0, 0.0]),
            _point(other_doc, 2, [1.0, 0.0, 0.0, 0.0]),  # 벡터는 같아도 다른 조직이라 빠져야 한다
        ]
    )
    client = VectorSearchClient(QdrantSettings(qdrant_collection="test_chunks"), ChunkContentReader(pool), store=store)

    hits = await client.search_points(vector=[1.0, 0.0, 0.0, 0.0], organization_id=1, limit=5)

    assert [h.document_id for h in hits] == [own_doc]
    assert hits[0].content == "같은 조직 청크 본문"


async def test_search_excludes_the_document_itself(pool, add_document, add_chunk, qdrant):
    store = ChunkVectorStore(QdrantSettings(qdrant_collection="test_chunks2"), client=qdrant, dimension=4)
    await store.ensure_collection()
    self_doc = await add_document(organization_id=1)
    other_doc = await add_document(organization_id=1)
    await add_chunk(document_id=other_doc, content="비교 대상 청크")
    await store.upsert([_point(self_doc, 1, [1.0, 0.0, 0.0, 0.0]), _point(other_doc, 1, [1.0, 0.0, 0.0, 0.0])])
    client = VectorSearchClient(QdrantSettings(qdrant_collection="test_chunks2"), ChunkContentReader(pool), store=store)

    hits = await client.search_points(vector=[1.0, 0.0, 0.0, 0.0], organization_id=1, exclude_doc_id=self_doc, limit=5)

    assert [h.document_id for h in hits] == [other_doc]


async def test_search_failure_returns_empty_list(pool):
    class BrokenStore:
        async def search(self, *args, **kwargs):
            raise ConnectionError("qdrant down")

    client = VectorSearchClient(QdrantSettings(), ChunkContentReader(pool), store=BrokenStore())

    assert await client.search_points(vector=[0.0], organization_id=1) == []

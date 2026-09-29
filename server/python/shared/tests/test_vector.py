from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator
from qdrant_client import AsyncQdrantClient

from history_shared.config import QdrantSettings
from history_shared.vector import (
    ChunkPoint,
    ChunkVectorStore,
    chunk_payload,
    point_id,
    scope_filter,
    scope_key,
)


def test_point_ids_match_java_golden(golden_dir, load_json):
    for case in load_json(golden_dir / "expected" / "point-ids.json"):
        assert point_id(case["documentId"], case["version"], case["chunkNo"]) == case["pointId"]


def test_scope_key():
    assert scope_key(1, 7) == "team:7"
    assert scope_key(1, None) == "org:1"


def test_scope_filter_rules():
    assert scope_filter([], system_admin=True) is None
    with pytest.raises(ValueError):
        scope_filter([])
    f = scope_filter(["org:1", "team:7"], denied_document_ids=[9])
    assert [c.match.value for c in f.should] == ["org:1", "team:7"]
    assert [c.match.value for c in f.must_not] == [9]
    assert scope_filter(["org:1"]).must_not is None


def test_chunk_payload_matches_contract_schema(contracts_dir, load_json):
    schema = load_json(contracts_dir / "qdrant-chunk-payload.schema.json")
    payload = chunk_payload(
        document_id=16,
        document_version=1,
        chunk_no=0,
        scope_key="org:1",
        chunk_tag="DECISION",
        document_type="POLICY_DOC",
        authored_at="2026-09-11",
        model_name="gemini-embedding-001",
        organization_id=1,
    )
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


@pytest.fixture
async def store():
    client = AsyncQdrantClient(location=":memory:")
    s = ChunkVectorStore(QdrantSettings(qdrant_collection="test_chunks"), client=client, dimension=4)
    yield s
    await s.close()


def _point(doc: int, key: str, vector: list[float]) -> ChunkPoint:
    return ChunkPoint(
        id=point_id(doc, 1, 0),
        vector=vector,
        payload=chunk_payload(
            document_id=doc,
            document_version=1,
            chunk_no=0,
            scope_key=key,
            chunk_tag="BACKGROUND",
            document_type="POLICY_DOC",
            authored_at=None,
            model_name="test",
            organization_id=1,
            dim=4,
        ),
    )


async def test_store_lifecycle_with_scope_filter(store):
    assert await store.ensure_collection() is True
    assert await store.ensure_collection() is False

    await store.upsert(
        [
            _point(1, "org:1", [1.0, 0.0, 0.0, 0.0]),
            _point(2, "team:7", [0.9, 0.1, 0.0, 0.0]),
            _point(3, "org:2", [0.8, 0.2, 0.0, 0.0]),
        ]
    )
    query = [1.0, 0.0, 0.0, 0.0]

    def docs(points):
        return {p.payload["document_id"] for p in points}

    assert docs(await store.search(query, query_filter=scope_filter(["org:1", "team:7"]), limit=10)) == {1, 2}
    assert docs(await store.search(query, query_filter=scope_filter(["org:1", "team:7"], denied_document_ids=[2]), limit=10)) == {1}
    assert docs(await store.search(query, query_filter=None, limit=10)) == {1, 2, 3}

    # 같은 point ID 로 다시 넣으면 덮어쓴다
    await store.upsert([_point(1, "org:1", [0.0, 1.0, 0.0, 0.0])])
    assert await store.count() == 3

    await store.delete_document(1)
    assert await store.count() == 2
    await store.upsert([])


async def test_ensure_collection_does_not_treat_errors_as_missing():
    client = AsyncMock()
    client.collection_exists.side_effect = ConnectionError("qdrant down")
    store = ChunkVectorStore(QdrantSettings(), client=client)
    with pytest.raises(ConnectionError):
        await store.ensure_collection()
    client.create_collection.assert_not_called()

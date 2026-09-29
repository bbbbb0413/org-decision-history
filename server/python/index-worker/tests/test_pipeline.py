"""문서 하나를 색인하는 흐름. 실제 PostgreSQL + 메모리 Qdrant + 로컬 임베딩으로 돌린다."""
from datetime import date

import json

import pytest
from fakes import FakeEvents, FakeFiles
from golden import CASES, EXPECTED, GOLDEN, input_bytes, load
from history_shared.config import QdrantSettings
from history_shared.embeddings import Embedder, EmbeddingError, LocalHashingEmbedder
from history_shared.vector import ChunkVectorStore
from jsonschema import Draft202012Validator
from qdrant_client import AsyncQdrantClient

from app.chunks_store import ChunkStore
from app.documents import DocumentReader
from app.pipeline import IndexingError, IndexingPipeline, IndexStage

PAYLOAD_SCHEMA = load(GOLDEN.parents[1] / "qdrant-chunk-payload.schema.json")


@pytest.fixture
async def qdrant():
    client = AsyncQdrantClient(location=":memory:")
    yield client
    await client.close()


@pytest.fixture
async def vectors(qdrant):
    store = ChunkVectorStore(QdrantSettings(qdrant_collection="document_chunks"), client=qdrant)
    await store.ensure_collection()
    return store


def pipeline_of(
    pool, vectors, *, files=None, embedder: Embedder | None = None, events=None
) -> IndexingPipeline:
    return IndexingPipeline(
        DocumentReader(pool),
        files or FakeFiles(),
        embedder or LocalHashingEmbedder(),
        vectors,
        ChunkStore(pool),
        events,
    )


async def chunk_rows(pool, document_id: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT chunk_no AS "chunkNo", section_path AS "sectionPath", chunk_tag::text AS "chunkTag",
               token_count AS "tokenCount", vector_point_id::text AS "pointId", content,
               document_version, scope_key, search_vector::text AS search_vector,
               page_start, page_end, bboxes
        FROM indexing.document_chunk WHERE document_id = $1 ORDER BY document_version, chunk_no
        """,
        document_id,
    )
    return [dict(row) for row in rows]


async def points_of(qdrant, document_id: int):
    points, _ = await qdrant.scroll("document_chunks", limit=1000, with_payload=True, with_vectors=True)
    return sorted((p for p in points if p.payload["document_id"] == document_id), key=lambda p: p.payload["chunk_no"])


def golden_case(case_id: str) -> tuple[dict, list[dict]]:
    case = next(c for c in CASES["documentCases"] if c["id"] == case_id)
    return case, load(EXPECTED / "document" / f"{case_id}.json")["chunks"]


async def add_golden_document(add_document, case: dict, **extra) -> int:
    header = case["header"]
    return await add_document(
        document_id=case["documentId"],
        title=header["title"],
        authored_at=date.fromisoformat(header["authoredAt"]),
        project_name=header["projectName"],
        organization_id=case["organizationId"],
        team_id=case["teamId"],
        version=case["version"],
        **extra,
    )


async def test_file_document_matches_golden_in_both_indexes(pool, add_document, vectors, qdrant):
    case, expected = golden_case("md-edge-cases")
    document_id = await add_golden_document(
        add_document, case, content="화면 본문은 원본 파일이 있으면 쓰지 않는다", document_type="POLICY_DOC",
        file=("docs/101/v3/policy.md", case["filename"]),
    )
    files = FakeFiles({"docs/101/v3/policy.md": input_bytes(case)})

    count = await pipeline_of(pool, vectors, files=files).index(document_id)

    rows = await chunk_rows(pool, document_id)
    assert count == len(expected) == len(rows)
    assert [{k: row[k] for k in ("chunkNo", "sectionPath", "chunkTag", "tokenCount", "pointId", "content")} for row in rows] == expected
    assert {row["scope_key"] for row in rows} == {"team:7"}
    assert all(row["search_vector"] for row in rows)
    # md는 페이지 개념이 없다
    assert all(row["page_start"] is None and row["bboxes"] is None for row in rows)

    points = await points_of(qdrant, document_id)
    assert [p.id for p in points] == [c["pointId"] for c in expected]
    embedder = LocalHashingEmbedder()
    validator = Draft202012Validator(PAYLOAD_SCHEMA)
    for point, chunk in zip(points, expected, strict=True):
        validator.validate(point.payload)
        assert point.payload == {
            "document_id": 101,
            "document_version": 3,
            "chunk_no": chunk["chunkNo"],
            "scope_key": "team:7",
            "chunk_tag": chunk["chunkTag"],
            "document_type": "POLICY_DOC",
            "authored_at": "2024-03-15",
            "model_name": embedder.model_name,
            "organization_id": 1,
            "dim": 768,
        }
    # 청크 본문(헤더 포함)을 임베딩한다
    assert points[0].vector == pytest.approx(embedder.vectorize(expected[0]["content"]), abs=1e-6)


async def test_pdf_document_stores_page_and_bboxes(pool, add_document, vectors):
    """바운딩 박스. PDF만 page_start·page_end·bboxes를 채운다."""
    case, expected = golden_case("pdf-ops-meeting")
    document_id = await add_golden_document(
        add_document, case, content="화면 본문은 쓰지 않는다", file=("docs/103/v2/ops.pdf", case["filename"])
    )
    files = FakeFiles({"docs/103/v2/ops.pdf": input_bytes(case)})

    count = await pipeline_of(pool, vectors, files=files).index(document_id)

    rows = await chunk_rows(pool, document_id)
    assert count == len(rows) == len(expected)
    for row in rows:
        assert row["page_start"] is not None and row["page_end"] is not None
        assert row["page_start"] <= row["page_end"]
        boxes = json.loads(row["bboxes"])
        assert boxes, "청크마다 최소 한 줄은 있어야 한다"
        for box in boxes:
            assert set(box) == {"page", "x", "y", "w", "h"}
            assert row["page_start"] <= box["page"] <= row["page_end"]
            assert 0.0 <= box["x"] <= 1.0 and 0.0 <= box["y"] <= 1.0
            assert box["w"] >= 0.0 and box["h"] >= 0.0


async def test_content_document_without_file_is_parsed_as_markdown(pool, add_document, vectors):
    case, expected = golden_case("content-crlf")
    document_id = await add_golden_document(add_document, case, content=case["content"])

    assert await pipeline_of(pool, vectors).index(document_id) == 2

    rows = await chunk_rows(pool, document_id)
    assert [{k: row[k] for k in ("chunkNo", "sectionPath", "chunkTag", "tokenCount", "pointId", "content")} for row in rows] == expected


async def test_reindexing_new_version_replaces_old_chunks_and_points(pool, add_document, vectors, qdrant):
    document_id = await add_document(content="# 결제\n환불 규정을 확정했다.\n# 배송\n배송비는 무료다.")
    pipeline = pipeline_of(pool, vectors)
    assert await pipeline.index(document_id) == 2

    await pool.execute(
        "UPDATE document.document SET current_version = 2, content = '# 결제\n환불은 7일 안에만 가능하다.' WHERE id = $1",
        document_id,
    )
    assert await pipeline.index(document_id) == 1

    rows = await chunk_rows(pool, document_id)
    assert [(row["document_version"], row["chunkNo"]) for row in rows] == [(2, 0)]
    points = await points_of(qdrant, document_id)
    assert [(p.payload["document_version"], p.payload["chunk_no"]) for p in points] == [(2, 0)]
    # 같은 버전을 다시 색인해도 point·행이 늘지 않는다
    assert await pipeline.index(document_id) == 1
    assert len(await chunk_rows(pool, document_id)) == 1 and len(await points_of(qdrant, document_id)) == 1


async def test_deleted_document_is_removed_from_both_indexes(pool, add_document, vectors, qdrant):
    document_id = await add_document()
    other_id = await add_document(title="다른 문서")
    pipeline = pipeline_of(pool, vectors)
    await pipeline.index(document_id)
    await pipeline.index(other_id)

    await pool.execute("UPDATE document.document SET deleted_at = now() WHERE id = $1", document_id)
    assert await pipeline.index(document_id) == 0

    assert await chunk_rows(pool, document_id) == [] and await points_of(qdrant, document_id) == []
    assert len(await chunk_rows(pool, other_id)) == 1 and len(await points_of(qdrant, other_id)) == 1


async def expect_failure(pipeline: IndexingPipeline, document_id: int) -> IndexingError:
    with pytest.raises(IndexingError) as error:
        await pipeline.index(document_id)
    return error.value


async def test_missing_document_is_not_retryable(pool, vectors):
    error = await expect_failure(pipeline_of(pool, vectors), 404)

    assert (error.stage, error.retryable, str(error)) == (IndexStage.PARSE, False, "문서를 찾을 수 없습니다")


async def test_unsupported_file_is_not_retryable(pool, add_document, vectors):
    document_id = await add_document(file=("docs/legacy.hwp", "legacy.hwp"))
    files = FakeFiles({"docs/legacy.hwp": b"HWP"})

    error = await expect_failure(pipeline_of(pool, vectors, files=files), document_id)

    assert (error.stage, error.retryable, str(error)) == (IndexStage.PARSE, False, "지원하지 않는 파일 형식입니다: hwp")


async def test_storage_read_failure_is_retryable(pool, add_document, vectors):
    document_id = await add_document(file=("docs/missing.md", "missing.md"))

    error = await expect_failure(pipeline_of(pool, vectors), document_id)

    assert (error.stage, error.retryable) == (IndexStage.PARSE, True)
    assert str(error).startswith("원본을 읽을 수 없습니다")


async def test_blank_document_is_not_retryable(pool, add_document, vectors):
    document_id = await add_document(content=" \n\t ")

    error = await expect_failure(pipeline_of(pool, vectors), document_id)

    assert (error.stage, error.retryable) == (IndexStage.CHUNK, False)


class FailingEmbedder(LocalHashingEmbedder):
    def __init__(self, error: Exception):
        super().__init__()
        self._error = error

    async def embed_documents(self, texts):
        raise self._error


@pytest.mark.parametrize("retryable", [True, False])
async def test_embedding_failure_keeps_retryable_flag(pool, add_document, vectors, retryable):
    document_id = await add_document()
    embedder = FailingEmbedder(EmbeddingError("임베딩 요청 실패", retryable=retryable))

    error = await expect_failure(pipeline_of(pool, vectors, embedder=embedder), document_id)

    assert (error.stage, error.retryable) == (IndexStage.EMBED, retryable)


async def test_vector_store_failure_is_retryable_and_keeps_keyword_index(pool, add_document, vectors):
    document_id = await add_document()
    pipeline = pipeline_of(pool, vectors)
    await pipeline.index(document_id)

    class BrokenVectors:
        async def delete_document(self, document_id):
            raise ConnectionError("qdrant down")

    error = await expect_failure(pipeline_of(pool, BrokenVectors()), document_id)

    assert (error.stage, error.retryable) == (IndexStage.VECTOR_UPSERT, True)
    assert len(await chunk_rows(pool, document_id)) == 1


async def test_successful_index_publishes_document_indexed_event(pool, add_document, vectors):
    document_id = await add_document(
        content="# 결제\n환불 규정을 확정했다.\n# 배송\n배송비는 무료다.", organization_id=3, team_id=9
    )
    events = FakeEvents()

    count = await pipeline_of(pool, vectors, events=events).index(document_id)

    assert count == 2
    [event] = events.published
    assert (event.document_id, event.version, event.chunk_count, event.organization_id, event.team_id) == (
        document_id, 1, 2, 3, 9,
    )


async def test_event_publish_failure_is_retryable_but_keeps_both_indexes(pool, add_document, vectors, qdrant):
    document_id = await add_document()

    error = await expect_failure(pipeline_of(pool, vectors, events=FakeEvents(fail=True)), document_id)

    assert (error.stage, error.retryable) == (IndexStage.EVENT_PUBLISH, True)
    # 재시도로 색인을 다시 돌려도 되게, 이미 만든 청크·벡터는 그대로 남아 있어야 한다
    assert len(await chunk_rows(pool, document_id)) == 1
    assert len(await points_of(qdrant, document_id)) == 1


async def test_no_publisher_configured_skips_event_silently(pool, add_document, vectors):
    document_id = await add_document()

    assert await pipeline_of(pool, vectors, events=None).index(document_id) == 1

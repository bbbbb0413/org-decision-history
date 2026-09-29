"""출처 문서 정보 결합 + 권한 재확인 + 바운딩 박스."""
import json

import pytest
from app.auth_client import PermissionScope
from app.search.models import SearchHit
from app.search.source_resolver import SourceResolver


def hit(document_id: int, *, document_version: int = 1, chunk_no: int = 0) -> SearchHit:
    return SearchHit(
        document_id=document_id,
        document_version=document_version,
        chunk_no=chunk_no,
        content="본문",
        section_path=None,
        chunk_tag=None,
        score=0.9,
        vector_similarity=0.9,
    )


@pytest.mark.asyncio
async def test_pdf_chunk_returns_page_and_bboxes(pool, add_document, add_chunk):
    document_id = await add_document(organization_id=1, with_file=True)
    boxes = json.dumps([{"page": 1, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}])
    await add_chunk(document_id=document_id, page_start=1, page_end=1, bboxes=boxes, scope_key="org:1")

    sources = await SourceResolver(pool).resolve(
        [hit(document_id)], PermissionScope(organization_ids=frozenset({1}))
    )

    assert len(sources) == 1
    assert sources[0]["pageStart"] == 1
    assert sources[0]["pageEnd"] == 1
    assert sources[0]["bboxes"] == [{"page": 1, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}]
    assert sources[0]["fileId"] is not None


@pytest.mark.asyncio
async def test_non_pdf_chunk_returns_none_bboxes(pool, add_document, add_chunk):
    document_id = await add_document(organization_id=1)
    await add_chunk(document_id=document_id, scope_key="org:1")

    sources = await SourceResolver(pool).resolve(
        [hit(document_id)], PermissionScope(organization_ids=frozenset({1}))
    )

    assert len(sources) == 1
    assert sources[0]["pageStart"] is None
    assert sources[0]["pageEnd"] is None
    assert sources[0]["bboxes"] is None


@pytest.mark.asyncio
async def test_missing_chunk_row_does_not_fail_resolution(pool, add_document):
    """청크가 (드물게) 이미 지워졌어도 문서 자체 정보는 여전히 돌려준다."""
    document_id = await add_document(organization_id=1)

    sources = await SourceResolver(pool).resolve(
        [hit(document_id)], PermissionScope(organization_ids=frozenset({1}))
    )

    assert len(sources) == 1
    assert sources[0]["pageStart"] is None and sources[0]["bboxes"] is None


@pytest.mark.asyncio
async def test_out_of_scope_document_is_dropped_without_error(pool, add_document, add_chunk):
    document_id = await add_document(organization_id=2)
    await add_chunk(document_id=document_id, scope_key="org:2")

    sources = await SourceResolver(pool).resolve(
        [hit(document_id)], PermissionScope(organization_ids=frozenset({1}))
    )

    assert sources == []

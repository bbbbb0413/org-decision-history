"""골든 스냅샷(contracts/indexing/golden)과 같은 결과가 나오는지 본다.

규칙을 의도해서 바꿨으면 스냅샷을 검토한 뒤 갱신한다(golden/README.md).
"""
import pytest
from golden import CASES, EXPECTED, chunker_input, header_of, input_bytes, load, utf16_offset_to_index
from history_shared.vector import point_id, scope_key

from app.chunk import Chunk, chunk_document
from app.parse import ParseError, parse_document


def chunk_json(chunk: Chunk, pid: str | None = None) -> dict:
    out = {
        "chunkNo": chunk.chunk_no,
        "sectionPath": chunk.section_path,
        "chunkTag": chunk.tag,
        "tokenCount": chunk.token_count,
    }
    if pid is not None:
        out["pointId"] = pid
    out["content"] = chunk.content
    return out


@pytest.mark.parametrize("case", CASES["chunkerCases"], ids=lambda c: c["id"])
def test_chunker_matches_java_golden(case):
    chunks = chunk_document(chunker_input(case), header_of(case))

    actual = {"id": case["id"], "chunks": [chunk_json(chunk) for chunk in chunks]}
    assert actual == load(EXPECTED / "chunker" / f"{case['id']}.json")


@pytest.mark.parametrize("case", CASES["documentCases"], ids=lambda c: c["id"])
def test_document_pipeline_matches_java_golden(case):
    expected = load(EXPECTED / "document" / f"{case['id']}.json")
    actual = {
        "id": case["id"],
        "filename": case["filename"],
        "scopeKey": scope_key(case["organizationId"], case["teamId"]),
    }
    try:
        parsed = parse_document(input_bytes(case), case["filename"])
    except ParseError as e:
        # 메시지 뒷부분은 라이브러리 오류 문구라 구현마다 다르다. 앞부분과 재시도 여부만 계약으로 본다
        actual["error"] = {"retryable": e.retryable, "messagePrefix": str(e).split(": ", 1)[0]}
        assert actual == expected
        return

    # 스냅샷의 헤딩 offset 은 UTF-16 위치다. 같은 글자를 가리키는지 Python 위치로 바꿔 비교한다
    text = expected["parsed"]["text"]
    expected["parsed"]["headings"] = [
        {**h, "offset": utf16_offset_to_index(text, h["offset"])} for h in expected["parsed"]["headings"]
    ]
    actual["parsed"] = {
        "text": parsed.text,
        "headings": [{"level": h.level, "title": h.title, "offset": h.offset} for h in parsed.headings],
    }
    actual["chunks"] = [
        chunk_json(chunk, point_id(case["documentId"], case["version"], chunk.chunk_no))
        for chunk in chunk_document(parsed, header_of(case))
    ]
    assert actual == expected

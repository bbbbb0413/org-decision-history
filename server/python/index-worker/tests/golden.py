"""골든 스냅샷 읽기 도우미(contracts/indexing/golden)."""
import json
from datetime import date
from pathlib import Path

from app.chunk import ChunkHeader
from app.parse import Heading, ParsedDocument


def _find_golden() -> Path:
    for directory in Path(__file__).resolve().parents:
        golden = directory / "contracts" / "indexing" / "golden"
        if (golden / "cases.json").is_file():
            return golden
    raise RuntimeError("contracts/indexing/golden/cases.json 을 찾을 수 없다")


GOLDEN = _find_golden()
EXPECTED = GOLDEN / "expected"
CASES = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def header_of(case: dict) -> ChunkHeader:
    h = case["header"]
    return ChunkHeader(
        title=h["title"],
        authored_at=date.fromisoformat(h["authoredAt"]) if h["authoredAt"] else None,
        project_name=h["projectName"],
    )


def input_bytes(case: dict) -> bytes:
    return (GOLDEN / case["file"]).read_bytes() if "file" in case else case["content"].encode("utf-8")


def chunker_input(case: dict) -> ParsedDocument:
    text = case["text"]
    return ParsedDocument(
        text,
        tuple(Heading(level=h["level"], title=h["title"], offset=text.index(h["at"])) for h in case["headings"]),
    )


def utf16_offset_to_index(text: str, offset: int) -> int:
    """UTF-16 위치를 Python 문자열 위치로 바꾼다."""
    units = 0
    for index, ch in enumerate(text):
        if units >= offset:
            return index
        units += 2 if ord(ch) > 0xFFFF else 1
    return len(text)

"""문서를 검색 단위로 나눈다.

규약: contracts/indexing/README.md §4·§5. 헤딩 구조를 먼저 경계로 쓰고, 700토큰을 넘는 절만 줄 단위로 다시 나눈다.
계약의 알려진 동작(코드 펜스 안 헤딩 등, 계약 "알려진 동작과 한계")도 그대로 따른다.
"""
import re
from dataclasses import dataclass
from datetime import date

from history_shared.text import estimate_tokens, is_java_whitespace, java_is_blank

from app.parse import JAVA_DOT, JAVA_LINE_START, JAVA_SPACE, LineBox, ParsedDocument

TARGET_TOKENS = 400
MAX_TOKENS = 700
OVERLAP_TOKENS = 60

# 표와 코드 블록은 중간에서 자르면 뜻이 무너지므로 끊지 않는다
_CODE_FENCE = re.compile(JAVA_LINE_START + "```")
_TABLE_ROW = re.compile(JAVA_SPACE + r"*\|" + JAVA_DOT + r"*\|" + JAVA_SPACE + "*")

# Java CASE_INSENSITIVE 는 ASCII 에만 적용된다. re.ASCII 와 함께 쓰면 같다
_FLAGS = re.IGNORECASE | re.ASCII
_TAG_RULES = (
    ("DECISION", re.compile("결정|채택|확정|승인|선정|합의|reject|approve|decide", _FLAGS)),
    ("ACTION_ITEM", re.compile("액션|todo|담당자|기한|follow[- ]?up|action item", _FLAGS)),
    ("DISCUSSION", re.compile("논의|검토|의견|대안|비교|제안|discuss|review", _FLAGS)),
)


@dataclass(frozen=True)
class Chunk:
    chunk_no: int
    content: str  # 헤더가 붙은 본문
    section_path: str
    tag: str
    token_count: int
    # PDF에서만 채운다. bboxes 원소는 {"page", "x", "y", "w", "h"}(0~1 정규화)
    page_start: int | None = None
    page_end: int | None = None
    bboxes: list[dict] | None = None


@dataclass(frozen=True)
class ChunkHeader:
    """청크 앞에 붙이는 문서 맥락. 청크만 보고도 어느 문서의 어디인지 알 수 있어야 한다."""

    title: str
    authored_at: date | None
    project_name: str | None

    def render(self, section_path: str | None) -> str:
        header = "[문서] " + self.title
        if self.authored_at is not None:
            header += " | 작성일 " + self.authored_at.isoformat()
        if self.project_name is not None and not java_is_blank(self.project_name):
            header += " | 프로젝트 " + self.project_name
        if section_path is not None and not java_is_blank(section_path):
            header += " | 절 " + section_path
        return header


def chunk_document(parsed: ParsedDocument, header: ChunkHeader) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, section_start, body in _split_by_headings(parsed):
        for piece_start, piece_end, piece in _split_by_length(body):
            if java_is_blank(piece):
                continue
            chunks.append(
                _build_chunk(len(chunks), parsed, header, path, section_start + piece_start, section_start + piece_end, piece)
            )

    # 절이 원문 전체를 덮으므로 사실상 일어나지 않지만 계약대로 둔다
    if not chunks and not java_is_blank(parsed.text):
        chunks.append(_build_chunk(0, parsed, header, "", 0, len(parsed.text), parsed.text))
    return chunks


def _build_chunk(chunk_no: int, parsed: ParsedDocument, header: ChunkHeader, path: str, start: int, end: int, piece: str) -> Chunk:
    """piece 는 parsed.text[start:end] 와 같다(길이 분할 겹침 구간 포함 시 원문과 다시 겹칠 수 있다)."""
    strip_start, strip_end = _strip_bounds(piece)
    stripped = piece[strip_start:strip_end]
    content = header.render(path) + "\n\n" + stripped
    page_start, page_end, bboxes = _bboxes_of(parsed.lines, start + strip_start, start + strip_end)
    return Chunk(chunk_no, content, path, tag_of(piece), estimate_tokens(content), page_start, page_end, bboxes)


def _strip_bounds(text: str) -> tuple[int, int]:
    """String.strip() 이 남기는 범위. java_strip(text) == text[start:end]."""
    start, end = 0, len(text)
    while start < end and is_java_whitespace(text[start]):
        start += 1
    while end > start and is_java_whitespace(text[end - 1]):
        end -= 1
    return start, end


def _bboxes_of(lines: tuple[LineBox, ...], start: int, end: int) -> tuple[int | None, int | None, list[dict] | None]:
    """청크 [start, end) 구간과 겹치는 PDF 줄들의 좌표. 한 줄이라도 걸치면 포함한다."""
    if not lines or start >= end:
        return None, None, None
    covered = [line for line in lines if line.start < end and line.end > start]
    if not covered:
        return None, None, None
    boxes = [
        {
            "page": line.page,
            "x": round(line.x0, 4),
            "y": round(line.top, 4),
            "w": round(max(0.0, line.x1 - line.x0), 4),
            "h": round(max(0.0, line.bottom - line.top), 4),
        }
        for line in covered
    ]
    pages = [line.page for line in covered]
    return min(pages), max(pages), boxes


def tag_of(body: str) -> str:
    for tag, pattern in _TAG_RULES:
        if pattern.search(body):
            return tag
    return "BACKGROUND"


def _split_by_headings(parsed: ParsedDocument) -> list[tuple[str, int, str]]:
    """(절 경로, 원문 안 시작 offset, 절 본문) 목록."""
    text, headings = parsed.text, parsed.headings
    if not headings:
        return [("", 0, text)]

    sections: list[tuple[str, int, str]] = []
    # 첫 헤딩 앞의 도입부도 버리지 않는다
    if headings[0].offset > 0:
        sections.append(("", 0, text[: headings[0].offset]))

    path_stack: list[str | None] = [None] * 7
    for i, heading in enumerate(headings):
        path_stack[heading.level] = heading.title
        for deeper in range(heading.level + 1, len(path_stack)):
            path_stack[deeper] = None
        end = headings[i + 1].offset if i + 1 < len(headings) else len(text)
        path = " > ".join(title for title in path_stack[1:] if title is not None)
        sections.append((path, heading.offset, text[heading.offset : end]))
    return sections


def _split_by_length(body: str) -> list[tuple[int, int, str]]:
    """(절 본문 안 시작 offset, 끝 offset, 조각 본문) 목록. 겹침 구간이 있으면 앞 조각과 offset이 겹친다."""
    if estimate_tokens(body) <= MAX_TOKENS:
        return [(0, len(body), body)]

    lines = body.split("\n")
    line_offsets: list[int] = []
    pos = 0
    for line in lines:
        line_offsets.append(pos)
        pos += len(line) + 1  # 잘려나간 "\n" 만큼(마지막 줄은 실제로 없지만 뒤에서 쓰지 않는다)

    def span(indices: list[int]) -> tuple[int, int, str]:
        last = indices[-1]
        return line_offsets[indices[0]], line_offsets[last] + len(lines[last]), "\n".join(lines[j] for j in indices)

    pieces: list[tuple[int, int, str]] = []
    buffer: list[int] = []
    buffer_tokens = 0
    inside_code = False
    for i, line in enumerate(lines):
        was_inside_code = inside_code
        if _CODE_FENCE.search(line):
            inside_code = not inside_code
        # 여는·닫는 펜스 줄 자신도 끊지 않는다(전·후 상태 중 하나라도 코드 안)
        unbreakable = was_inside_code or inside_code or _TABLE_ROW.fullmatch(line) is not None

        line_tokens = estimate_tokens(line)
        if not unbreakable and buffer_tokens + line_tokens > TARGET_TOKENS and buffer:
            pieces.append(span(buffer))
            buffer = _tail_for_overlap(buffer, lines)
            buffer_tokens = estimate_tokens("\n".join(lines[j] for j in buffer))
        buffer.append(i)
        buffer_tokens += line_tokens
    if buffer:
        pieces.append(span(buffer))
    return pieces


def _tail_for_overlap(indices: list[int], lines: list[str]) -> list[int]:
    """겹침 구간. 뒤에서부터 누적 토큰이 목표에 닿을 때까지 줄을 가져온다."""
    tail: list[int] = []
    tokens = 0
    for i in reversed(indices):
        if tokens >= OVERLAP_TOKENS:
            break
        tail.insert(0, i)
        tokens += estimate_tokens(lines[i])
    return tail

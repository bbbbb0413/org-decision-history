"""원본 파일에서 본문과 헤딩 구조를 뽑는다.

규약: contracts/indexing/README.md §3·§7. 서식은 재현하지 않는다. 검색과 인용에 필요한 것은 글자와 구조다.
헤딩 offset 은 Python 문자열 위치(코드 포인트)다. 골든 스냅샷은 UTF-16 위치로 적혀 있어 테스트가 변환해 비교한다.
"""
import datetime as dt
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import docx
import openpyxl
import pdfplumber
import pptx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from history_shared.text import java_is_blank, java_trim
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect

# Java 정규식 MULTILINE ^ : 입력 시작 또는 줄 끝 문자(\n \r \u0085 \u2028 \u2029) 뒤. \r\n 사이는 줄 시작이 아니다
JAVA_LINE_START = r"(?:(?<![\s\S])|(?<=[\n\u0085\u2028\u2029])|(?<=\r)(?!\n))"
# Java 기본 \s 는 ASCII 공백만, . 는 줄 끝 문자를 뺀 모든 문자다
JAVA_SPACE = r"[ \t\n\x0b\x0c\r]"
JAVA_DOT = r"[^\n\r\u0085\u2028\u2029]"
JAVA_LINE_END = r"(?=[\n\r\u0085\u2028\u2029]|\Z)"

# 마크다운 ATX 헤딩 ^(#{1,6})\s+(.+)$ (MULTILINE)
_MD_HEADING = re.compile(JAVA_LINE_START + r"(#{1,6})" + JAVA_SPACE + "+(" + JAVA_DOT + "+)" + JAVA_LINE_END)
# Word 의 Heading1~6 스타일 ID
_HEADING_STYLE = re.compile(r"heading\s*([1-6])", re.IGNORECASE | re.ASCII)


@dataclass(frozen=True)
class Heading:
    level: int  # 1 이 최상위
    title: str
    offset: int  # text 안에서 이 헤딩이 시작하는 위치


@dataclass(frozen=True)
class LineBox:
    """PDF 한 줄의 위치. 페이지 좌상단이 원점인 0~1 정규화 좌표계다.

    start·end 는 text 안에서 이 줄이 차지하는 범위(코드 포인트, 줄바꿈 문자는 제외)다.
    """

    start: int
    end: int
    page: int  # 1부터 시작
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    headings: tuple[Heading, ...] = ()
    # PDF만 채운다. 청크가 원문의 어느 줄에 걸치는지 계산하는 재료다(chunk.py)
    lines: tuple[LineBox, ...] = ()


class ParseError(Exception):
    """파싱 실패. retryable 이 False 면 다시 시도해도 결과가 같다(암호화·손상·미지원 형식)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def extension_of(filename: str | None) -> str:
    if filename is None:
        return ""
    dot = filename.rfind(".")
    return "" if dot < 0 else filename[dot + 1 :].lower()


def parse_document(data: bytes, filename: str | None) -> ParsedDocument:
    extension = extension_of(filename)
    if extension == "md":
        return parse_markdown(data.decode("utf-8", "replace"))
    if extension == "txt":
        # 일반 텍스트는 헤딩 표기가 없다. 짧은 줄을 소제목으로 짐작하면 분할이 어긋나므로 길이로만 나눈다
        return ParsedDocument(data.decode("utf-8", "replace"))
    if extension == "pdf":
        return parse_pdf(data)
    if extension == "docx":
        return parse_docx(data)
    if extension == "pptx":
        return parse_pptx(data)
    if extension == "xlsx":
        return parse_xlsx(data)
    raise ParseError(f"지원하지 않는 파일 형식입니다: {extension}")


def parse_markdown(text: str) -> ParsedDocument:
    headings = tuple(
        Heading(level=len(m.group(1)), title=java_trim(m.group(2)), offset=m.start()) for m in _MD_HEADING.finditer(text)
    )
    return ParsedDocument(text, headings)


# ---- 표 ----
#
# 표는 마크다운 파이프 행(`| 셀 | 셀 |`)으로 적는다. 새 표현을 만들지 않은 이유는 청커가
# 이미 그 모양을 알기 때문이다 — `^\s*\|.*\|\s*$` 에 걸리는 줄은 길이 분할에서 끊지 않는다
# (계약 §4.2). 표를 다른 모양으로 적으면 행 중간에서 잘려 "누가 승인했는지"가 두 청크로
# 갈린다.
#
# 머리글 구분선(`|---|`)은 붙이지 않는다. 첫 행이 머리글인지 파일에서 알 수 없고, 아니라면
# 데이터 한 줄을 머리글로 읽게 만든다. 모르는 것을 적지 않는 편이 낫다.
_MAX_CELL_CHARS = 500


def _cell_text(value: object) -> str:
    """셀 하나를 파이프 행에 넣을 수 있는 한 줄로 만든다."""
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        # 엑셀은 날짜도 숫자로 들고 있다. str() 을 그대로 쓰면 "2026-05-18 00:00:00" 이 된다
        text = value.date().isoformat() if value.time() == dt.time.min else value.isoformat(sep=" ")
    elif isinstance(value, (dt.date, dt.time)):
        text = value.isoformat()
    elif isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
    else:
        text = str(value)
    # 셀 안 줄바꿈이 남으면 한 행이 여러 줄로 갈려 파이프 행이 아니게 된다.
    # `|` 도 그대로 두면 열 수가 어긋나므로 이스케이프한다.
    text = java_trim(text.replace("\r", " ").replace("\n", " ").replace("|", r"\|"))
    return text if len(text) <= _MAX_CELL_CHARS else text[:_MAX_CELL_CHARS]


def _table_row(cells: Iterable[object]) -> str:
    """셀이 전부 비면 빈 문자열을 준다(호출자가 그 행을 버린다)."""
    rendered = [_cell_text(cell) for cell in cells]
    while rendered and not rendered[-1]:
        rendered.pop()  # 오른쪽 빈 열은 버린다. 엑셀은 쓰지 않은 열도 행에 담아 준다
    if not rendered:
        return ""
    return "| " + " | ".join(rendered) + " |"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


# 업로드 용량은 50MB로 막혀 있지만, 그 안에서도 페이지가 아주 많은(수천 쪽) PDF는
# asyncio.to_thread로 넘겨도 스레드 풀 슬롯 하나를 동기로 오래 붙잡는다.
# 실제 사내 문서(정책·회의록)가 이 정도로 길 일은 없어 넉넉히 잡는다.
MAX_PDF_PAGES = 500

# ---- PDF 헤딩 추정 ----
#
# PDF 에는 마크다운 `#` 이나 Word 의 Heading 스타일 같은 "이건 제목이다"라는 표시가 없다.
# 글자 크기밖에 없어서 추정할 수밖에 없고, 추정은 틀릴 수 있다.
#
# 그래서 **틀릴 때 지금보다 나빠지지 않는 쪽**으로 기울였다. 확신이 없으면 헤딩을 만들지 않고
# 종전처럼 문서 전체를 한 덩어리로 둔다(길이 분할만 걸린다). 헤딩을 잘못 만들면 절 경계가
# 엉뚱한 곳에 생겨 인용이 "이 문서의 어디"를 틀리게 가리키는데, 그건 구조가 없는 것보다 나쁘다.
#
# 판정은 본문 글자 크기 대비 비율로 한다. 절대 크기(예: 14pt 이상)로 하면 문서마다 기준
# 글자가 달라 그대로 깨진다.
_PDF_HEADING_SIZE_RATIO = 1.15
# 제목은 짧다. 이보다 긴 줄은 큰 글씨로 쓴 본문으로 본다.
_PDF_HEADING_MAX_CHARS = 120
# **본문이 글자 수의 이만큼을 차지해야** 크기 분석을 믿는다.
#
# 처음에는 "헤딩 후보가 전체 줄의 35% 를 넘으면 포기"로 잡았는데 질문이 틀렸다. 그 기준은
# 제목이 많은 짧은 문서를 벌한다 — 데모 시드 PDF 가 0.32 로 간신히 통과했고, 절이 하나만
# 늘어도 구조를 전부 잃었다. 정작 막아야 하는 실패는 "본문 크기를 잘못 짚는 것"이므로
# 그걸 직접 본다: 한 크기가 글자 수의 과반을 차지하면 그게 본문이라고 볼 근거가 된다.
# 과반을 차지하는 크기가 없으면(표지, 표, 크기가 뒤섞인 문서) 추정을 포기한다.
_PDF_BODY_MIN_SHARE = 0.5
# 절 경로는 `path_stack[1:7]` 이라 7 이상은 담기지 않는다(chunk.py `_split_by_headings`).
_PDF_HEADING_MAX_LEVEL = 6


@dataclass(frozen=True)
class PdfLine:
    """헤딩 추정에 필요한 만큼만 들고 있는 한 줄. `infer_pdf_headings` 의 입력이다."""

    text: str
    offset: int  # text 안에서 이 줄이 시작하는 위치
    size: float  # 그 줄 글자 크기의 중앙값


def _median_size(chars: list) -> float:
    sizes = [float(c["size"]) for c in chars if c.get("size") is not None]
    if not sizes:
        return 0.0
    sizes.sort()
    middle = len(sizes) // 2
    return sizes[middle] if len(sizes) % 2 else (sizes[middle - 1] + sizes[middle]) / 2


def _body_size(lines: list[PdfLine], weights: dict[float, int]) -> float:
    """본문 글자 크기. 줄 수가 아니라 **글자 수**로 센다.

    줄 수로 세면 제목만 여러 줄인 표지에서 제목 크기가 본문으로 잡힌다. 본문은 글자가 많다.
    """
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda kv: (kv[1], kv[0]))[0]


def infer_pdf_headings(lines: list[PdfLine], weights: dict[float, int]) -> tuple[Heading, ...]:
    """글자 크기로 헤딩을 추정한다. 확신이 없으면 빈 튜플을 돌려준다."""
    body = _body_size(lines, weights)
    if body <= 0 or len(lines) < 3:
        return ()

    # 본문이 과반이 아니면 무엇이 본문인지 모르는 것이다. 그때는 자르지 않는다.
    total_chars = sum(weights.values())
    if total_chars <= 0 or weights.get(body, 0) / total_chars < _PDF_BODY_MIN_SHARE:
        return ()

    threshold = body * _PDF_HEADING_SIZE_RATIO
    candidates = [
        line for line in lines
        if line.size >= threshold and 0 < len(line.text.strip()) <= _PDF_HEADING_MAX_CHARS
    ]
    if not candidates:
        return ()

    # 큰 글씨가 상위 수준이다. 크기 종류가 여럿이면 순서대로 1,2,3… 을 준다.
    ranks = sorted({line.size for line in candidates}, reverse=True)
    level_of = {size: min(i + 1, _PDF_HEADING_MAX_LEVEL) for i, size in enumerate(ranks)}

    return tuple(
        Heading(level=level_of[line.size], title=java_trim(line.text), offset=line.offset)
        for line in candidates
    )


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ParseError(
                    f"PDF 페이지 수가 너무 많습니다({len(pdf.pages)}쪽, 최대 {MAX_PDF_PAGES}쪽)"
                )
            # extract_text_lines 로 본문을 다시 만들면(줄마다 이어 붙이기) extract_text() 와 글자 하나까지 같다
            # (골든 픽스처로 확인했다). 그 대신 줄마다 좌표를 함께 얻는다(바운딩 박스).
            # 쪽마다 끝에 줄바꿈을 붙인다(계약 §3)
            parts: list[str] = []
            lines: list[LineBox] = []
            # 헤딩 추정용. 글자 크기는 본문에는 남기지 않으므로 여기서만 들고 있는다.
            text_lines: list[PdfLine] = []
            size_weights: dict[float, int] = {}
            offset = 0
            for page_no, page in enumerate(pdf.pages, start=1):
                width, height = (page.width or 1.0), (page.height or 1.0)
                page_lines = page.extract_text_lines(layout=False)
                if not page_lines:
                    parts.append("\n")
                    offset += 1
                    continue
                for line in page_lines:
                    line_text = line["text"]
                    parts.append(line_text)
                    start = offset
                    offset += len(line_text)
                    lines.append(
                        LineBox(
                            start=start,
                            end=offset,
                            page=page_no,
                            x0=_clamp01(line["x0"] / width),
                            top=_clamp01(line["top"] / height),
                            x1=_clamp01(line["x1"] / width),
                            bottom=_clamp01(line["bottom"] / height),
                        )
                    )
                    chars = line.get("chars") or []
                    size = _median_size(chars)
                    text_lines.append(PdfLine(text=line_text, offset=start, size=size))
                    size_weights[size] = size_weights.get(size, 0) + len(chars)
                    parts.append("\n")
                    offset += 1
            text = "".join(parts)
            headings = infer_pdf_headings(text_lines, size_weights)
    except ParseError:
        raise
    except Exception as e:  # noqa: BLE001 — 손상 파일은 라이브러리마다 다른 예외를 던진다
        if _is_encrypted(e):
            raise ParseError("암호화된 파일이라 본문을 추출할 수 없습니다") from None
        raise ParseError(f"PDF 를 읽을 수 없습니다: {e}") from None
    return ParsedDocument(text, headings=headings, lines=tuple(lines))


def _is_encrypted(error: BaseException) -> bool:
    """pdfplumber 는 pdfminer 예외를 감싸서 던진다. 원인 사슬과 인자를 따라가며 확인한다."""
    seen: set[int] = set()
    pending: list[object] = [error]
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (PDFPasswordIncorrect, PDFEncryptionError)):
            return True
        pending.extend([current.__cause__, current.__context__, *current.args])
    return False


def _docx_blocks(document) -> Iterator[Paragraph | Table]:
    """문단과 표를 **문서에 놓인 순서대로** 준다.

    `document.paragraphs` 는 표를 건너뛰고, `document.tables` 는 위치를 잃는다. 표가 어느
    문단 뒤에 있었는지는 절 경로(어느 헤딩 아래인가)를 정하는 데 필요하므로 본문 XML 을
    직접 훑는다.
    """
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _docx_row_cells(row) -> list[str]:
    """한 행의 셀 글자. 가로로 병합된 칸은 한 번만 센다.

    `row.cells` 는 격자 열마다 셀을 주므로 병합된 칸이 같은 값으로 반복된다. 그대로 쓰면
    `| 승인 | 승인 | 승인 |` 처럼 적혀 사람이 읽어도 표가 이상하다.
    """
    values: list[str] = []
    previous = None
    for cell in row.cells:
        if cell._tc is previous:
            continue
        previous = cell._tc
        values.append(cell.text)
    return values


def parse_docx(data: bytes) -> ParsedDocument:
    """본문 문단과 표를 읽는다. 머리글·바닥글·각주·텍스트 상자는 빠진다.

    승인 이력·담당자 표가 문서의 결론인 경우가 많다. 표를 버리면 "누가 언제 승인했는가"를
    검색으로 찾을 수 없어 파이프 행으로 남긴다.
    """
    try:
        document = docx.Document(io.BytesIO(data))
        parts: list[str] = []
        headings: list[Heading] = []
        length = 0
        for block in _docx_blocks(document):
            if isinstance(block, Table):
                for row in block.rows:
                    # 중첩 표는 펼치지 않는다. cell.text 가 안쪽 표의 글자까지 이어 주므로
                    # 글자는 남고 안쪽 표의 행 구분만 사라진다
                    line = _table_row(_docx_row_cells(row))
                    if not line:
                        continue
                    parts.append(line + "\n")
                    length += len(line) + 1
                continue
            line = block.text
            if line is None or java_is_blank(line):
                parts.append("\n")
                length += 1
                continue
            level = _heading_level(block._p.style)
            if level is not None:
                headings.append(Heading(level=level, title=java_trim(line), offset=length))
            parts.append(line + "\n")
            length += len(line) + 1
        return ParsedDocument("".join(parts), tuple(headings))
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"docx 를 읽을 수 없습니다: {e}") from None


def _heading_level(style_id: str | None) -> int | None:
    """문단 스타일 ID(표시 이름이 아니다)에서 헤딩 수준을 얻는다. 한국어 Word 의 지역화된 ID 는 잡히지 않는다."""
    if not style_id:
        return None
    match = _HEADING_STYLE.search(style_id)
    return int(match.group(1)) if match else None


# ---- pptx ----
#
# 장 하나가 슬라이드 하나다. PDF 처럼 크기를 추정할 필요가 없다 — 제목 개체 틀이 "이건
# 제목이다"를 명시한다. 제목이 없는 슬라이드는 `슬라이드 N` 로 부른다. 지어낸 이름이지만
# 절 경로가 비는 것보다 낫다. 사람이 원본을 열어 그 장으로 갈 수 있다.
MAX_PPTX_SLIDES = 300


def _pptx_shapes(shapes) -> Iterator[object]:
    """그룹 도형을 펼쳐 실제 글자를 가진 도형만 준다. 실제 발표자료는 도형을 자주 묶는다."""
    for shape in shapes:
        inner = getattr(shape, "shapes", None)
        if inner is not None and not getattr(shape, "has_text_frame", False):
            yield from _pptx_shapes(inner)
        else:
            yield shape


def parse_pptx(data: bytes) -> ParsedDocument:
    """슬라이드마다 제목을 헤딩으로 두고 글자와 표를 순서대로 잇는다.

    발표자 노트·차트 데이터·SmartArt 는 읽지 않는다(계약 §3 참고).
    """
    try:
        presentation = pptx.Presentation(io.BytesIO(data))
        slides = list(presentation.slides)
    except Exception as e:  # noqa: BLE001 — 손상 파일은 라이브러리마다 다른 예외를 던진다
        raise ParseError(f"pptx 를 읽을 수 없습니다: {e}") from None

    if len(slides) > MAX_PPTX_SLIDES:
        raise ParseError(f"슬라이드 수가 너무 많습니다({len(slides)}장, 최대 {MAX_PPTX_SLIDES}장)")

    try:
        parts: list[str] = []
        headings: list[Heading] = []
        length = 0

        def emit(line: str) -> int:
            nonlocal length
            start = length
            parts.append(line + "\n")
            length += len(line) + 1
            return start

        for number, slide in enumerate(slides, start=1):
            title_shape = slide.shapes.title
            title = java_trim(title_shape.text) if title_shape is not None and title_shape.has_text_frame else ""
            if title:
                headings.append(Heading(level=1, title=title, offset=emit(title)))
            else:
                # 지어낸 제목은 본문에 적지 않는다. 절 경로에만 쓴다
                headings.append(Heading(level=1, title=f"슬라이드 {number}", offset=length))

            title_element = None if title_shape is None else title_shape._element
            for shape in _pptx_shapes(slide.shapes):
                # python-pptx 는 훑을 때마다 도형 객체를 새로 만든다. `is` 로는 제목 도형을
                # 다시 만나도 같은 것인지 알 수 없어 XML 요소로 비교한다
                if title_element is not None and shape._element is title_element:
                    continue
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        line = _table_row(cell.text for cell in row.cells)
                        if line:
                            emit(line)
                    continue
                if not getattr(shape, "has_text_frame", False):
                    continue
                for paragraph in shape.text_frame.paragraphs:
                    line = paragraph.text
                    if line and not java_is_blank(line):
                        emit(line)
        return ParsedDocument("".join(parts), tuple(headings))
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"pptx 를 읽을 수 없습니다: {e}") from None


# ---- xlsx ----
#
# 시트 하나가 절 하나다. 시트 이름이 절 경로가 되므로 인용이 "어느 시트"까지 가리킨다.
#
# 행 수에는 상한을 둔다. PDF·pptx 는 쪽·장 수가 너무 많으면 **거절**하지만(그 정도로 긴
# 문서는 이 시스템이 다루는 종류가 아니다) 스프레드시트는 정당한 문서도 행이 수만 줄일 수
# 있다. 거절하면 그 문서는 지식으로 아예 들어오지 못한다. 그래서 앞부분만 색인하고 **잘랐다는
# 사실을 본문에 남긴다** — 인용을 읽는 사람이 뒤가 빠졌다는 것을 볼 수 있어야 한다.
MAX_XLSX_ROWS = 2000
_XLSX_TRUNCATED = "〈행이 너무 많아 이후 내용은 색인하지 않았습니다〉"


def parse_xlsx(data: bytes) -> ParsedDocument:
    """시트마다 이름을 헤딩으로 두고 셀을 파이프 행으로 적는다.

    수식은 **엑셀이 마지막에 저장한 계산 결과**를 쓴다(`data_only=True`). 계산된 적이 없는
    수식 칸은 빈 칸이 된다 — 수식 문자열을 그대로 색인하면 `=SUM(B2:B9)` 가 검색에 걸려
    답변이 엉뚱해진다. 차트·이미지·주석은 읽지 않는다.
    """
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"xlsx 를 읽을 수 없습니다: {e}") from None

    try:
        parts: list[str] = []
        headings: list[Heading] = []
        length = 0
        remaining = MAX_XLSX_ROWS

        def emit(line: str) -> int:
            nonlocal length
            start = length
            parts.append(line + "\n")
            length += len(line) + 1
            return start

        for sheet in workbook.worksheets:
            name = java_trim(sheet.title or "")
            headings.append(Heading(level=1, title=name, offset=emit(name) if name else length))
            for values in sheet.iter_rows(values_only=True):
                if remaining <= 0:
                    break
                line = _table_row(values)
                if not line:
                    continue  # 빈 행은 상한에서도 세지 않는다
                remaining -= 1
                emit(line)
            if remaining <= 0:
                emit(_XLSX_TRUNCATED)
                break
        return ParsedDocument("".join(parts), tuple(headings))
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"xlsx 를 읽을 수 없습니다: {e}") from None
    finally:
        workbook.close()

"""파서 규칙 중 골든 스냅샷이 다루지 않는 경계."""
from pathlib import Path

import pytest
from golden import GOLDEN
from pdfminer.pdfdocument import PDFPasswordIncorrect

import app.parse as parse_module
from app.parse import (
    ParseError,
    _cell_text,
    _heading_level,
    _is_encrypted,
    _table_row,
    extension_of,
    parse_document,
    parse_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_markdown_headings_follow_java_line_rules():
    text = "소개 ## 둘째\r\n# 셋째 본문\r# 넷째\n#붙임\n####### 일곱\n# 공백\n#   다섯  \n"

    headings = [(h.level, h.title, h.offset) for h in parse_markdown(text).headings]

    assert headings == [
        #   뒤도 줄 시작이다. 제목은 \r 앞에서 끝난다
        (2, "둘째", text.index("## 둘째")),
        #   도 줄 끝이다
        (1, "셋째", text.index("# 셋째")),
        # \r 하나만 있어도 줄이 바뀐다
        (1, "넷째", text.index("# 넷째")),
        # '#붙임'(공백 없음), '#######'(7개), NBSP(Java \s 가 아니다)는 헤딩이 아니다. 제목 앞뒤 공백은 trim
        (1, "다섯", text.index("#   다섯")),
    ]


def test_extension_is_case_insensitive_and_last_dot():
    assert extension_of("회의록.final.PDF") == "pdf"
    assert extension_of("README") == ""
    assert extension_of(None) == ""


def test_txt_has_no_headings():
    parsed = parse_document("# 제목처럼 보여도\n일반 텍스트다".encode(), "notes.txt")

    assert parsed.headings == ()


def test_encrypted_pdf_is_not_retryable():
    with pytest.raises(ParseError) as error:
        parse_document((FIXTURES / "encrypted.pdf").read_bytes(), "secret.pdf")

    assert str(error.value) == "암호화된 파일이라 본문을 추출할 수 없습니다"
    assert error.value.retryable is False


def test_encryption_is_detected_through_wrapped_errors():
    assert _is_encrypted(RuntimeError("pdfminer", PDFPasswordIncorrect()))
    wrapped = ValueError("outer")
    wrapped.__cause__ = PDFPasswordIncorrect()
    assert _is_encrypted(wrapped)
    assert not _is_encrypted(ValueError("broken xref"))


def test_corrupt_docx_is_not_retryable():
    with pytest.raises(ParseError) as error:
        parse_document(b"not a zip", "broken.docx")

    assert str(error.value).startswith("docx 를 읽을 수 없습니다")
    assert error.value.retryable is False


def test_heading_level_uses_style_id():
    assert _heading_level("Heading2") == 2
    assert _heading_level("heading 3") == 3
    assert _heading_level("Title") is None
    assert _heading_level(None) is None


def test_pdf_with_too_many_pages_is_rejected_before_extraction(monkeypatch):
    """병적인 PDF가 워커 스레드를 오래 붙잡지 않게, 페이지를 훑기 전에 먼저 거른다."""
    monkeypatch.setattr(parse_module, "MAX_PDF_PAGES", 1)
    data = (GOLDEN / "fixtures" / "ops-meeting.pdf").read_bytes()  # 이 픽스처는 2쪽이다

    with pytest.raises(ParseError) as error:
        parse_document(data, "ops-meeting.pdf")

    assert "페이지 수가 너무 많습니다" in str(error.value)
    assert error.value.retryable is False


def test_pdf_lines_cover_the_whole_text_with_valid_normalized_coordinates():
    data = (GOLDEN / "fixtures" / "ops-meeting.pdf").read_bytes()

    parsed = parse_document(data, "ops-meeting.pdf")

    assert parsed.lines, "PDF는 줄 좌표를 채워야 한다"
    pages = {line.page for line in parsed.lines}
    assert pages == {1, 2}, "이 픽스처는 2쪽이다"
    for line in parsed.lines:
        # offset이 실제 그 줄의 글자와 일치해야 한다(줄바꿈은 포함하지 않는다)
        assert parsed.text[line.start : line.end] == parsed.text[line.start : line.end].rstrip("\n")
        assert parsed.text[line.end : line.end + 1] in ("\n", "")
        assert 0.0 <= line.x0 <= line.x1 <= 1.0
        assert 0.0 <= line.top <= line.bottom <= 1.0
    # 줄은 원문 등장 순서대로, 서로 겹치지 않아야 한다
    for a, b in zip(parsed.lines, parsed.lines[1:], strict=False):
        assert a.end < b.start


# ---- PDF 헤딩 추정 (2-1) ----
#
# PDF 에는 "이건 제목이다"라는 표시가 없어 글자 크기로 추정한다. 추정이 틀릴 때
# **지금보다 나빠지지 않는 것**이 설계 목표다 — 확신이 없으면 헤딩을 만들지 않는다.
# 여기서 지키는 것은 그 보수성이다. 임계값을 완화하다 이 테스트를 깨면, 잘못 자른 절 때문에
# 인용이 "이 문서의 어디"를 틀리게 가리키기 시작한다.

def pdf_lines(*specs: tuple[str, float]) -> tuple[list, dict[float, int]]:
    """(본문, 크기) 목록으로 PdfLine 과 크기별 글자 수 가중치를 만든다."""
    from app.parse import PdfLine

    lines: list[PdfLine] = []
    weights: dict[float, int] = {}
    offset = 0
    for text, size in specs:
        lines.append(PdfLine(text=text, offset=offset, size=size))
        weights[size] = weights.get(size, 0) + len(text)
        offset += len(text) + 1
    return lines, weights


def infer(*specs: tuple[str, float]):
    from app.parse import infer_pdf_headings

    lines, weights = pdf_lines(*specs)
    return infer_pdf_headings(lines, weights)


def test_pdf_큰_글씨를_헤딩으로_본다():
    headings = infer(
        ("보안 로그 보관 기간", 18.0),
        ("배경", 14.0),
        ("로그를 90일만 보관하고 있었다. 침해 조사에서 4개월 전 기록이 필요했다.", 11.0),
        ("결정 사항", 14.0),
        ("인증 로그는 1년, 접근 로그는 180일로 확정한다.", 11.0),
    )

    assert [(h.level, h.title) for h in headings] == [
        (1, "보안 로그 보관 기간"),
        (2, "배경"),
        (2, "결정 사항"),
    ]


def test_pdf_본문은_글자_수로_정한다():
    """줄 수로 세면 제목이 여러 줄인 표지에서 제목 크기가 본문으로 잡힌다."""
    headings = infer(
        ("제목 첫 줄", 20.0),
        ("제목 둘째 줄", 20.0),
        ("제목 셋째 줄", 20.0),
        ("본문은 한 줄이지만 글자가 훨씬 많다. " * 6, 10.0),
    )

    # 20pt 줄이 더 많지만 글자 수로는 10pt 가 본문이다 → 20pt 가 헤딩이어야 한다
    assert [h.title for h in headings] == ["제목 첫 줄", "제목 둘째 줄", "제목 셋째 줄"]


def test_pdf_크기가_한_종류면_헤딩이_없다():
    # 글자 크기로만 판정하므로 변화가 없으면 구조를 알 수 없다. 종전 동작(청크 1개)을 유지한다.
    assert infer(
        ("첫 줄", 11.0),
        ("둘째 줄", 11.0),
        ("셋째 줄", 11.0),
    ) == ()


def test_pdf_본문이_과반이_아니면_포기한다():
    """무엇이 본문인지 모르면 자르지 않는다.

    처음에는 "헤딩 후보가 전체 줄의 35%를 넘으면 포기"로 잡았는데 질문이 틀렸다. 그 기준은
    제목이 많은 짧은 문서를 벌한다 — 데모 시드 PDF 가 0.32 로 간신히 통과하고 있었고, 절이
    하나만 늘어도 구조를 전부 잃었다. 막아야 하는 실패는 "본문 크기를 잘못 짚는 것"이므로
    그걸 직접 본다.
    """
    # 세 크기가 글자 수를 비슷하게 나눠 가져 과반이 없다
    assert infer(
        ("가나다라마바사", 20.0),
        ("아자차카타파하", 14.0),
        ("거너더러머버서", 10.0),
    ) == ()


def test_pdf_긴_줄은_제목으로_보지_않는다():
    long_line = "큰 글씨로 쓴 문장이지만 제목이 아니다. " * 8
    headings = infer(
        ("짧은 제목", 16.0),
        (long_line, 16.0),
        ("본문 " * 40, 11.0),
        ("본문 " * 40, 11.0),
    )

    assert [h.title for h in headings] == ["짧은 제목"]


def test_pdf_줄이_너무_적으면_헤딩을_만들지_않는다():
    assert infer(("제목", 18.0), ("본문", 11.0)) == ()


def test_pdf_크기_종류가_많아도_수준은_6을_넘지_않는다():
    # 절 경로는 path_stack[1:7] 이라 7 이상은 담기지 않는다(chunk.py)
    body = ("본문 " * 60, 10.0)
    specs = [(f"제목{i}", float(30 - i)) for i in range(8)] + [body, body, body, body, body,
                                                               body, body, body, body, body,
                                                               body, body, body, body, body,
                                                               body, body, body, body, body]
    from app.parse import infer_pdf_headings
    lines, weights = pdf_lines(*specs)
    headings = infer_pdf_headings(lines, weights)

    assert headings, "제목이 하나도 안 잡혔다"
    assert max(h.level for h in headings) <= 6


def test_pdf_골든_픽스처에서_절이_갈리고_좌표도_남는다():
    """실제 PDF 로 확인한다 — 절 구조와 좌표를 함께 얻어야 한다."""
    from pathlib import Path

    from app.parse import parse_document

    for directory in Path(__file__).resolve().parents:
        fixture = directory / "contracts" / "indexing" / "golden" / "fixtures" / "ops-meeting.pdf"
        if fixture.is_file():
            break
    else:
        raise AssertionError("ops-meeting.pdf 골든 픽스처를 찾지 못했다")

    parsed = parse_document(fixture.read_bytes(), "ops-meeting.pdf")

    assert [(h.level, h.title) for h in parsed.headings] == [
        (1, "분기 운영 회의록"),
        (2, "1. 배경"),
        (2, "2. 결정 사항"),
        (2, "3. 지표 표"),
        (2, "4. 후속 조치"),
    ]
    assert parsed.lines, "줄 좌표가 사라졌다 — 헤딩을 얻으려고 좌표를 잃으면 안 된다"


# ---- 표 구조 보존 · pptx · xlsx (2-2) ----
#
# 표는 마크다운 파이프 행으로 적는다. 새 표현을 만들지 않은 이유는 청커가 이미 그 모양을
# 알기 때문이다 — `|...|` 줄은 길이 분할에서 끊지 않아 표 한 행이 두 청크로 갈리지 않는다.


def test_표_행은_파이프_행으로_적는다():
    assert _table_row(["구분", "A안", "B안"]) == "| 구분 | A안 | B안 |"
    # 셀 안 줄바꿈이 남으면 한 행이 여러 줄로 갈려 파이프 행이 아니게 된다
    assert _table_row(["비고", "3건 회수\n재검토"]) == "| 비고 | 3건 회수 재검토 |"
    # 셀 안 파이프를 그대로 두면 열 수가 어긋난다
    assert _table_row(["비고", "A | B"]) == r"| 비고 | A \| B |"
    # 오른쪽 빈 열은 버린다. 엑셀은 쓰지 않은 열도 행에 담아 준다
    assert _table_row(["값", "", None, ""]) == "| 값 |"
    # 전부 비면 행 자체를 버린다(호출자가 판단한다)
    assert _table_row([None, "", "  "]) == ""


def test_셀_값은_형식별로_읽을_수_있게_적는다():
    from datetime import date, datetime

    # 엑셀은 날짜도 숫자로 들고 있다. str() 을 그대로 쓰면 "2026-03-02 00:00:00" 이 된다
    assert _cell_text(datetime(2026, 3, 2, 0, 0)) == "2026-03-02"
    assert _cell_text(datetime(2026, 3, 2, 14, 30)) == "2026-03-02 14:30:00"
    assert _cell_text(date(2026, 3, 2)) == "2026-03-02"
    assert _cell_text(True) == "TRUE"
    assert _cell_text(None) == ""
    # 한 셀이 청크를 다 먹지 않게 자른다
    assert len(_cell_text("가" * 900)) == 500


def test_docx_표는_문단_사이_제_위치에_남는다():
    """표를 버리면 "누가 얼마로 결정했는지"가 본문에 없다.

    승인 이력·비용 비교가 표에 있는 문서가 흔하다. 표가 **어느 헤딩 아래**에 있었는지도 지켜야
    인용이 절을 맞게 가리킨다.
    """
    parsed = parse_document(_golden_fixture("infra-decision.docx").read_bytes(), "infra-decision.docx")

    lines = parsed.text.splitlines()
    assert "| 구분 | A안 | B안 |" in lines
    assert "| 월 비용 | 120만원 | 80만원 |" in lines
    # 표는 '대안 비교' 헤딩 뒤, '결정' 헤딩 앞이다
    assert lines.index("대안 비교") < lines.index("| 구분 | A안 | B안 |") < lines.index("결정")


def test_pptx_는_슬라이드마다_절이_갈린다():
    parsed = parse_document(_golden_fixture("access-review-deck.pptx").read_bytes(), "deck.pptx")

    assert [(h.level, h.title) for h in parsed.headings] == [
        (1, "접근권한 관리 개선 제안"),
        (1, "현황"),
        (1, "대안 비교"),
        # 제목 개체 틀이 없는 슬라이드. 지어낸 이름이지만 절 경로가 비는 것보다 낫다
        (1, "슬라이드 4"),
        (1, "결정"),
    ]
    lines = parsed.text.splitlines()
    # 제목을 두 번 적지 않는다(제목 도형은 슬라이드 도형 목록에도 들어 있다)
    assert lines.count("현황") == 1
    assert "| 도입 기간 | 4개월 | 6주 |" in lines
    # 묶은 도형 안의 글자도 읽는다. 실제 발표자료는 도형을 자주 묶는다
    assert "검토 주기를 월 1회로 줄인다" in lines
    assert "예외 승인은 팀장 결재로 남긴다" in lines


def test_pptx_제목이_없는_슬라이드는_본문에_가짜_제목을_적지_않는다():
    parsed = parse_document(_golden_fixture("access-review-deck.pptx").read_bytes(), "deck.pptx")

    assert "슬라이드 4" not in parsed.text
    # 헤딩 offset 은 그 슬라이드의 첫 글자를 가리킨다
    slide4 = next(h for h in parsed.headings if h.title == "슬라이드 4")
    assert parsed.text[slide4.offset:].startswith("검토 주기를 월 1회로 줄인다")


def test_pptx_슬라이드가_너무_많으면_거절한다(monkeypatch):
    monkeypatch.setattr(parse_module, "MAX_PPTX_SLIDES", 2)

    with pytest.raises(ParseError) as error:
        parse_document(_golden_fixture("access-review-deck.pptx").read_bytes(), "deck.pptx")

    assert "슬라이드 수가 너무 많습니다" in str(error.value)
    assert error.value.retryable is False


def test_xlsx_는_시트마다_절이_갈린다():
    parsed = parse_document(_golden_fixture("access-review-log.xlsx").read_bytes(), "log.xlsx")

    assert [(h.level, h.title) for h in parsed.headings] == [(1, "검토이력"), (1, "합계")]
    lines = parsed.text.splitlines()
    assert lines[0] == "검토이력"
    assert "| 검토일 | 대상 시스템 | 검토자 | 결과 | 비고 |" in lines
    # 빈 행은 버린다
    assert "|  |" not in parsed.text
    # 수식은 엑셀이 저장한 계산 결과를 쓴다. openpyxl 로 만든 파일에는 그 값이 없어 빈 칸이 된다
    assert lines[-1] == "| 합계 |"


def test_xlsx_행이_상한을_넘으면_잘랐다고_적는다(monkeypatch):
    """거절하지 않는다. 행이 많은 것은 정당한 문서일 수 있어서, 앞부분만 색인하고 사실을 남긴다."""
    monkeypatch.setattr(parse_module, "MAX_XLSX_ROWS", 2)

    parsed = parse_document(_golden_fixture("access-review-log.xlsx").read_bytes(), "log.xlsx")

    lines = parsed.text.splitlines()
    assert lines[-1] == "〈행이 너무 많아 이후 내용은 색인하지 않았습니다〉"
    assert "합계" not in lines  # 상한에 걸려 둘째 시트까지 가지 않았다


@pytest.mark.parametrize(
    ("filename", "prefix"),
    [("broken.pptx", "pptx 를 읽을 수 없습니다"), ("broken.xlsx", "xlsx 를 읽을 수 없습니다")],
)
def test_손상된_ooxml_은_재시도하지_않는다(filename, prefix):
    with pytest.raises(ParseError) as error:
        parse_document(b"not a zip", filename)

    assert str(error.value).startswith(prefix)
    assert error.value.retryable is False


def _golden_fixture(name: str) -> Path:
    return GOLDEN / "fixtures" / name

"""헤딩 우선 분할과 길이 보조 분할이 설계대로 동작하는지 본다."""
from datetime import date

from history_shared.text import estimate_tokens

from app.chunk import ChunkHeader, chunk_document
from app.parse import Heading, LineBox, ParsedDocument

HEADER = ChunkHeader("결제 정책 회의록", date(2024, 3, 15), "결제팀 프로젝트")


def chunk(text: str, *headings: tuple[int, str, str]):
    """headings 는 (level, title, 헤딩이 시작하는 문자열)."""
    parsed = ParsedDocument(text, tuple(Heading(level, title, text.index(at)) for level, title, at in headings))
    return chunk_document(parsed, HEADER)


def filler(lines: int) -> str:
    # 40자(10토큰) 줄을 100줄 반복하면 1000토큰으로 상한(700)을 넘는다
    return "".join(f"{i:04d} line of filler text for split \n" for i in range(lines))


def test_no_headings_produces_one_chunk_for_short_body():
    chunks = chunk("짧은 본문입니다.")

    assert len(chunks) == 1
    assert chunks[0].section_path == ""
    assert "짧은 본문입니다." in chunks[0].content


def test_blank_document_with_no_headings_produces_no_chunks():
    assert chunk("   ") == []


def test_chunk_header_renders_title_authored_at_project_and_section_path():
    assert chunk("본문")[0].content.startswith("[문서] 결제 정책 회의록 | 작성일 2024-03-15 | 프로젝트 결제팀 프로젝트")


def test_intro_before_first_heading_is_kept_as_separate_section():
    chunks = chunk("도입부입니다.\n# 첫 절\n본문 내용.", (1, "첫 절", "# 첫 절"))

    assert len(chunks) == 2
    assert chunks[0].section_path == ""
    assert "도입부입니다." in chunks[0].content
    assert chunks[1].section_path == "첫 절"


def test_nested_headings_build_hierarchical_section_path():
    chunks = chunk(
        "# 결제 정책\n상위 절 본문\n## 환불 규정\n하위 절 본문",
        (1, "결제 정책", "# 결제 정책"),
        (2, "환불 규정", "## 환불 규정"),
    )

    assert [c.section_path for c in chunks] == ["결제 정책", "결제 정책 > 환불 규정"]


def test_sibling_heading_drops_deeper_level_from_path():
    # H2 아래 있던 절이 끝나고 새 H1 로 돌아오면 이전 H2 경로가 남으면 안 된다
    chunks = chunk(
        "# 결제\n## 환불\n환불 본문\n# 배송\n배송 본문",
        (1, "결제", "# 결제"),
        (2, "환불", "## 환불"),
        (1, "배송", "# 배송"),
    )

    assert [c.section_path for c in chunks] == ["결제", "결제 > 환불", "배송"]


def test_keywords_tag_chunks():
    assert chunk("이번 안건은 A안으로 확정 결정했다.")[0].tag == "DECISION"
    assert chunk("담당자는 다음 주까지 문서를 정리한다. 기한을 지킨다.")[0].tag == "ACTION_ITEM"
    assert chunk("여러 대안을 놓고 논의했으나 결론은 못 냈다.")[0].tag == "DISCUSSION"
    assert chunk("오늘 참석자는 다섯 명이었다.")[0].tag == "BACKGROUND"


def test_decision_keyword_takes_priority_over_action_keyword():
    # 한 청크 안에 결정과 액션아이템 단서가 함께 있으면 결정을 우선한다
    assert chunk("A안으로 결정했다. 담당자는 다음 주까지 후속 조치한다.")[0].tag == "DECISION"


def test_section_longer_than_max_tokens_is_split_into_multiple_pieces():
    chunks = chunk(filler(100))

    assert len(chunks) > 1
    assert "0000 line of filler" in chunks[0].content
    assert "0099 line of filler" in chunks[-1].content


def test_adjacent_split_pieces_overlap_at_the_boundary():
    chunks = chunk(filler(100))

    assert len(chunks) > 1
    # 겹침 구간이 있다면 첫 조각의 마지막 줄이 둘째 조각에도 나타나야 한다
    assert chunks[0].content.split("\n")[-1] in chunks[1].content


def test_code_block_is_not_split_across_pieces():
    # 코드 줄만으로 1000토큰 넘게 채워 줄 단위 분할 경로를 타게 한다
    body = (
        "도입 설명 줄입니다.\n```\n"
        + "".join(f"code line {i:04d} of a long fenced block here\n" for i in range(100))
        + "```\n마무리 설명 줄입니다.\n"
    )
    assert estimate_tokens(body) > 700, "이 테스트가 분할 경로를 타려면 본문이 MAX_TOKENS 를 넘어야 한다"

    chunks = chunk(body)

    assert len(chunks) > 1, "분할이 실제로 일어났어야 이 테스트가 의미가 있다"
    assert any(c.content.count("```") == 2 for c in chunks), "여는 펜스와 닫는 펜스가 같은 청크 안에 있어야 한다"


def test_pdf_lines_produce_page_and_bbox_range():
    text = "1쪽 첫줄\n1쪽 둘째줄\n2쪽 첫줄\n"
    lines = (
        LineBox(start=0, end=6, page=1, x0=0.1, top=0.05, x1=0.5, bottom=0.08),
        LineBox(start=7, end=14, page=1, x0=0.1, top=0.10, x1=0.6, bottom=0.13),
        LineBox(start=15, end=20, page=2, x0=0.1, top=0.05, x1=0.4, bottom=0.08),
    )
    parsed = ParsedDocument(text, lines=lines)

    [c] = chunk_document(parsed, HEADER)

    assert (c.page_start, c.page_end) == (1, 2)
    assert [b["page"] for b in c.bboxes] == [1, 1, 2]
    assert c.bboxes[0] == {"page": 1, "x": 0.1, "y": 0.05, "w": 0.4, "h": 0.03}


def test_heading_split_chunks_get_only_their_own_page_lines():
    text = "# 1쪽 절\n1쪽 본문\n# 2쪽 절\n2쪽 본문\n"
    headings = (
        Heading(level=1, title="1쪽 절", offset=text.index("# 1쪽 절")),
        Heading(level=1, title="2쪽 절", offset=text.index("# 2쪽 절")),
    )
    lines = (
        LineBox(start=text.index("# 1쪽 절"), end=text.index("# 1쪽 절") + 6, page=1, x0=0, top=0, x1=1, bottom=0.1),
        LineBox(start=text.index("1쪽 본문"), end=text.index("1쪽 본문") + 5, page=1, x0=0, top=0.1, x1=1, bottom=0.2),
        LineBox(start=text.index("# 2쪽 절"), end=text.index("# 2쪽 절") + 6, page=2, x0=0, top=0, x1=1, bottom=0.1),
        LineBox(start=text.index("2쪽 본문"), end=text.index("2쪽 본문") + 5, page=2, x0=0, top=0.1, x1=1, bottom=0.2),
    )
    parsed = ParsedDocument(text, headings, lines)

    first, second = chunk_document(parsed, HEADER)

    assert (first.page_start, first.page_end) == (1, 1)
    assert (second.page_start, second.page_end) == (2, 2)
    assert {b["page"] for b in first.bboxes} == {1}
    assert {b["page"] for b in second.bboxes} == {2}


def test_no_lines_leaves_bbox_fields_none():
    [c] = chunk("헤딩도 PDF 좌표도 없는 평문입니다.")

    assert (c.page_start, c.page_end, c.bboxes) == (None, None, None)


def test_long_body_split_keeps_bboxes_within_each_piece():
    body = filler(100)
    lines = tuple(
        LineBox(start=body.index(line), end=body.index(line) + len(line), page=1 + i // 40, x0=0, top=0, x1=1, bottom=0.02)
        for i, line in enumerate(body.rstrip("\n").split("\n"))
    )
    parsed = ParsedDocument(body, lines=lines)

    chunks = chunk_document(parsed, HEADER)

    assert len(chunks) > 1
    for c in chunks:
        assert c.page_start is not None and c.bboxes
        for box in c.bboxes:
            assert 1 <= box["page"] <= 3

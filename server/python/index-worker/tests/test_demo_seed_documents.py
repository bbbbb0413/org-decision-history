"""데모 시드 문서(`deploy/demo-seed/documents/`)가 색인 계약을 지키는지 본다.

이 파일들은 원본 파일이 붙은 데모 문서다(`deploy/demo-seed/README.md`). 검색·인용·하이라이트를
실제 파일로 보여주는 근거라 색인 계약을 지켜야 한다.

여기서 지키는 것은 두 가지다.

1. **마크다운 문서는 절 구조를 가진다.** 절 경로(`section_path`)가 비면 답변이 "이 문서의
   어디"를 가리키지 못하고, DECISION 절이 없으면 타임라인 발췌가 비어 있다
   (`contracts/indexing/README.md` §5). 문구를 손보다 이 조건을 깨면 화면에는 아무 오류도
   뜨지 않고 검색·타임라인만 조용히 나빠진다.
2. **PDF 도 좌표와 절 구조를 모두 준다.** 헤딩은 글자 크기로 추정하므로 문구·서식을
   바꾸면 깨질 수 있어 여기서 고정한다.
"""
from datetime import date
from pathlib import Path

import pytest

from app.chunk import ChunkHeader, chunk_document
from app.parse import parse_document

DOCUMENTS = Path(__file__).parents[4] / "deploy" / "demo-seed" / "documents"
HEADER = ChunkHeader("데모 문서", date(2026, 5, 18), None)


def chunks_of(path: Path):
    parsed = parse_document(path.read_bytes(), path.name)
    return parsed, chunk_document(parsed, HEADER)


def markdown_files() -> list[Path]:
    return sorted(DOCUMENTS.glob("*.md"))


def test_시드_문서_디렉터리가_있다():
    assert DOCUMENTS.is_dir(), f"{DOCUMENTS} 가 없다"
    assert markdown_files(), "마크다운 시드 문서가 하나도 없다"
    assert sorted(DOCUMENTS.glob("*.pdf")), "PDF 시드 문서가 없다 — 좌표 경로를 덮는 유일한 문서다"


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.stem)
def test_마크다운_문서는_절마다_청크가_갈린다(path: Path):
    parsed, chunks = chunks_of(path)
    assert len(parsed.headings) >= 4, f"{path.name}: 헤딩이 너무 적다"
    assert len(chunks) >= 4
    for chunk in chunks:
        assert chunk.section_path, f"{path.name}: 절 경로가 빈 청크가 있다 (chunk {chunk.chunk_no})"


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.stem)
def test_마크다운_문서에_DECISION_절이_있다(path: Path):
    # 타임라인 발췌는 chunk_tag='DECISION' 청크만 쓴다. 없으면 타임라인이 빈다.
    _, chunks = chunks_of(path)
    assert any(chunk.tag == "DECISION" for chunk in chunks), f"{path.name}: DECISION 절이 없다"


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.stem)
def test_마크다운_문서의_절_경로가_서로_다르다(path: Path):
    _, chunks = chunks_of(path)
    paths = [chunk.section_path for chunk in chunks]
    assert len(set(paths)) == len(paths), f"{path.name}: 절 경로가 겹친다 {paths}"


def test_회의록의_액션_아이템_절이_DECISION_으로_넘어가지_않는다():
    """`승인`·`결정` 같은 낱말 하나로 절 태그가 DECISION 으로 바뀐다(부분 일치).

    회의록의 액션 아이템 절이 DECISION 이 되면 타임라인 발췌가 오염된다. 실제로 예시 문구에
    "재승인"이 들어가 이 절이 DECISION 으로 잡혔던 적이 있어 문구를 고쳤다. 그 수정을 고정한다.
    """
    path = next(p for p in markdown_files() if "회의록" in p.name)
    _, chunks = chunks_of(path)
    action = next(c for c in chunks if c.section_path.endswith("액션 아이템"))
    assert action.tag == "ACTION_ITEM", f"액션 아이템 절이 {action.tag} 로 잡혔다"


def test_pdf_는_좌표와_절_구조를_모두_준다():
    """글자 크기로 추정한 헤딩으로 마크다운과 같은 절 구조를 갖는다 — 좌표를 잃지 않고서."""
    path = sorted(DOCUMENTS.glob("*.pdf"))[0]
    parsed, chunks = chunks_of(path)

    assert parsed.headings, "PDF 헤딩 추정이 동작하지 않는다"
    assert parsed.lines, "PDF 줄 좌표가 비었다"
    assert len(chunks) >= 4, "절이 갈리지 않았다"
    for chunk in chunks:
        assert chunk.section_path, f"절 경로가 빈 청크가 있다 (chunk {chunk.chunk_no})"
    assert any(chunk.tag == "DECISION" for chunk in chunks), "DECISION 절이 없다"

    with_boxes = [chunk for chunk in chunks if chunk.bboxes]
    assert with_boxes, "좌표를 가진 청크가 없다 — 절 구조를 얻으려고 좌표를 잃으면 안 된다"
    for chunk in with_boxes:
        assert chunk.page_start >= 1
        for box in chunk.bboxes:
            assert box["page"] >= 1
            # 좌표는 0~1 정규화다(page 좌상단 원점). 화면이 이 가정으로 겹쳐 그린다.
            for key in ("x", "y", "w", "h"):
                assert 0.0 <= box[key] <= 1.0, f"{key}={box[key]} 가 0~1 밖이다"

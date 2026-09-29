#!/usr/bin/env python3
"""데모 시드의 PDF 문서를 만든다.

PDF 를 하나 넣는 이유: 원본 파일이 붙은 문서 중 **좌표를 가진 청크**는 PDF 에서만 나온다
(`indexing.document_chunk.bboxes`). 그 좌표로 원본을 하이라이트하는 경로를 데모에서 보여준다.

만들어진 PDF 는 `documents/` 에 커밋해 둔다. 시드 스크립트가 reportlab 을 요구하지 않게 하려는
것이다 — 시드는 표준 라이브러리만으로 돌아야 한다. 이 스크립트는 그 PDF 를 다시 만들거나
내용을 고칠 때만 쓴다.

    python3 -m venv venv && ./venv/bin/pip install reportlab
    ./venv/bin/python make_pdf.py

PDF 메타데이터(제목·작성자·생성일)를 채우는 것이 중요하다. 일괄 가져오기의
`DocumentMetadataExtractor.fromPdf` 가 그 값을 문서 제목·작성자·작성일로 그대로 쓰기 때문이다.
비워 두면 본문 첫 줄이 제목이 되고 작성일이 가져온 날짜로 들어간다.

한글 폰트는 macOS 기본 AppleGothic 을 쓴다. 폰트를 넣지 않으면 한글이 전부 깨져서
pdfplumber 가 뽑는 글자도 깨진다 — 색인·검색이 조용히 망가지는 종류의 실패다.
"""
import datetime
import os
import pathlib
import sys

TITLE = "보안 로그 보관 기간 결정 기록"
AUTHOR = "이도윤"
CREATED = datetime.datetime(2026, 5, 18, 9, 30, tzinfo=datetime.timezone.utc)

# reportlab 은 생성일을 인자로 받지 않고 `TimeStamp` 가 SOURCE_DATE_EPOCH 를 먼저 본다.
# import 시점에 읽히므로 reportlab 을 불러오기 전에 넣어야 한다. 부수 효과로 PDF 가
# 바이트 단위로 재현 가능해져서, 내용을 바꾸지 않으면 다시 만들어도 diff 가 나지 않는다.
os.environ["SOURCE_DATE_EPOCH"] = str(int(CREATED.timestamp()))

from reportlab.lib.pagesizes import A4  # noqa: E402 — SOURCE_DATE_EPOCH 를 먼저 넣어야 한다
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]

OUT = pathlib.Path(__file__).parent / "documents" / "04_보안로그_보관기간_결정기록.pdf"

# 절 제목을 굵게 키워 사람이 읽기 좋게 만들지만, **PDF 에서 이것이 헤딩으로 인식되지는 않는다.**
# `parse_pdf` 는 줄 좌표만 뽑고 헤딩은 하나도 만들지 않아서(`ParsedDocument(text, lines=...)`),
# 이 문서의 청크는 section_path 가 빈 문자열이 된다. 마크다운 문서와 대비되는 지점이고,
# 데모에서 그 대비를 보여주는 것이 이 파일의 목적 중 하나다.
BODY = [
    ("h", "보안 로그 보관 기간 결정 기록"),
    ("p", "정보보안팀 / 2026-05-18 / 보안본부"),
    ("", ""),
    ("h2", "배경"),
    ("p", "보안 로그를 전부 90일만 보관하고 있었다. 2026-04 침해 조사에서 4개월 전 접근 기록이"),
    ("p", "필요했는데 이미 삭제돼 있어 유입 경로를 끝까지 추적하지 못했다."),
    ("p", "반대로 로그 저장 비용은 매달 늘어 작년 대비 2.3배가 됐다. 전부 오래 두는 것도 답이 아니다."),
    ("", ""),
    ("h2", "검토한 대안"),
    ("p", "- 전체 1년 보관 — 조사에는 충분하지만 저장 비용이 지금의 4배가 된다. 고르지 않았다."),
    ("p", "- 전체 90일 유지 + 필요 시 스냅샷 — 무엇이 필요할지 사고 전에는 알 수 없어 실효성이 낮다."),
    ("p", "- 로그 종류별 차등 보관 — 비용과 조사 가능성을 함께 만족한다. 이것을 택했다."),
    ("", ""),
    ("h2", "결정 사항"),
    ("p", "인증·권한 변경 로그는 1년, 데이터 접근 로그는 180일, 애플리케이션 디버그 로그는"),
    ("p", "30일 보관으로 확정한다. 법령이 더 긴 기간을 요구하는 항목은 법령을 따른다."),
    ("", ""),
    ("h2", "결정 근거"),
    ("p", "침해 조사에서 실제로 필요했던 것은 누가 언제 무엇에 접근했는지였고, 그 정보는"),
    ("p", "인증·권한·데이터 접근 로그에 있다. 디버그 로그는 조사에 쓰인 적이 없으면서 용량의"),
    ("p", "70%를 차지했다. 종류별로 나누면 조사 가능성은 오히려 늘고 비용은 줄어든다."),
    ("", ""),
    ("h2", "영향 범위"),
    ("p", "전 서비스의 로그 수집 설정과 보관 정책을 변경해야 한다. 플랫폼본부 인증팀·결제팀은"),
    ("p", "로그 종류 분류 작업이 필요하다."),
    ("", ""),
    ("h2", "재검토 시점"),
    ("p", "2027-05 정기 재검토한다. 저장 비용이 다시 2배가 되면 그 전에 다시 본다."),
]


def font_name() -> str:
    for path in FONT_CANDIDATES:
        if pathlib.Path(path).exists():
            pdfmetrics.registerFont(TTFont("Gothic", path))
            return "Gothic"
    sys.exit("한글 TTF 폰트를 찾지 못했다. FONT_CANDIDATES 에 경로를 추가해라.")


def main() -> None:
    font = font_name()
    c = canvas.Canvas(str(OUT), pagesize=A4)
    c.setTitle(TITLE)
    c.setAuthor(AUTHOR)
    c.setCreator("demo-seed/make_pdf.py")

    width, height = A4
    y = height - 25 * mm
    for kind, text in BODY:
        if y < 25 * mm:
            c.showPage()
            y = height - 25 * mm
        if kind == "h":
            c.setFont(font, 16)
            y -= 4 * mm
        elif kind == "h2":
            c.setFont(font, 12.5)
            y -= 3 * mm
        else:
            c.setFont(font, 10)
        c.drawString(22 * mm, y, text)
        y -= 7 * mm if kind in ("h", "h2") else 5.6 * mm

    c.save()
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

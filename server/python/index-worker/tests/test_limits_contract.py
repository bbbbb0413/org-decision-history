"""색인 단계의 xlsx 상한이 가져오기 단계(document-service DocumentMetadataExtractor)와 같은지 계약 파일로 확인한다."""
import json
from pathlib import Path

from app import parse


def _limits() -> dict:
    for directory in Path(__file__).resolve().parents:
        path = directory / "contracts" / "indexing" / "limits.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise RuntimeError("contracts/indexing/limits.json 을 찾을 수 없다")


def test_xlsx_limits_match_contract():
    limits = _limits()
    assert parse.MAX_XLSX_ROWS == limits["xlsxMaxRows"]
    assert parse._MAX_CELL_CHARS == limits["xlsxMaxCellChars"]

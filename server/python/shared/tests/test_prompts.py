"""프롬프트 파일 읽기 규칙."""
import pytest

from history_shared.prompts import load_prompt


def test_leading_comment_and_one_final_newline_are_removed(tmp_path):
    (tmp_path / "p.md").write_text("<!--\n설명\n-->\n첫 줄\n둘째 줄\n", encoding="utf-8")
    assert load_prompt(tmp_path, "p") == "첫 줄\n둘째 줄"


def test_prompt_ending_with_newline_keeps_it(tmp_path):
    (tmp_path / "p.md").write_text("끝에 줄바꿈이 있는 프롬프트\n\n", encoding="utf-8")
    assert load_prompt(tmp_path, "p") == "끝에 줄바꿈이 있는 프롬프트\n"


def test_placeholders_are_filled_only_when_given(tmp_path):
    (tmp_path / "p.md").write_text('최대 {n}개. 형식은 {"a": 1}', encoding="utf-8")
    assert load_prompt(tmp_path, "p") == '최대 {n}개. 형식은 {"a": 1}'
    assert load_prompt(tmp_path, "p", n=3) == '최대 3개. 형식은 {"a": 1}'
    with pytest.raises(ValueError):
        load_prompt(tmp_path, "p", missing=1)


def test_graph_extraction_prompt_is_loaded():
    from history_shared.graph.extractor import GRAPH_EXTRACTION_SYSTEM

    assert GRAPH_EXTRACTION_SYSTEM and "<!--" not in GRAPH_EXTRACTION_SYSTEM

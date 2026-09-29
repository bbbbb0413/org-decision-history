from history_shared.text import (
    estimate_tokens,
    is_java_whitespace,
    java_is_blank,
    java_strip,
    java_trim,
    utf16_units,
)


def test_java_trim_only_removes_control_and_space():
    assert java_trim("\x00\t 제목 \r\n") == "제목"
    # trim 은 U+0020 보다 큰 공백(전각 공백)을 지우지 않는다
    assert java_trim("　제목　") == "　제목　"


def test_token_estimates_match_java_golden(golden_dir, load_json):
    cases = load_json(golden_dir / "expected" / "token-estimates.json")
    assert cases, "골든 토큰 사례가 비어 있다"
    for case in cases:
        assert estimate_tokens(case["text"]) == case["estimate"], case["text"]


def test_none_is_zero_tokens():
    assert estimate_tokens(None) == 0


def test_utf16_units_split_supplementary_characters():
    assert len(utf16_units("a😀")) == 3


def test_java_whitespace_rules():
    # 계약(Java Character.isWhitespace)은 NBSP 계열을 공백으로 보지 않는다. Python str.isspace() 와 다르다
    for ch in "   ":
        assert not is_java_whitespace(ch)
    for ch in " \t\n\x0b\x0c\r\x1c\x1f　  ":
        assert is_java_whitespace(ch)


def test_java_strip_and_blank():
    assert java_strip("　 본문  \n\t") == "본문  "
    assert java_is_blank(" ") is False
    assert java_is_blank(" \t　") is True
    assert java_is_blank("") is True

"""색인 계약의 문자열 규칙.

계약은 공백 판정·길이를 Java String 의미(strip·isBlank·UTF-16 길이)로 정의한다. 청크 경계·토큰 수·
임베딩이 골든 스냅샷과 같아야 하므로 Python 기본 동작 대신 이 규칙을 따른다. 규약: contracts/indexing/README.md §6·§7.
"""
import math
import struct
import unicodedata

# Character.isWhitespace 가 공백으로 보는 제어 문자
_JAVA_CONTROL_WHITESPACE = frozenset("\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f")
# 공백 분류(Zs)지만 Java 가 공백으로 보지 않는 줄바꿈 금지 공백. Python str.strip() 은 이것도 지운다
_NON_BREAKING_SPACES = frozenset("   ")


def is_java_whitespace(ch: str) -> bool:
    """java.lang.Character.isWhitespace 와 같다."""
    if ch in _JAVA_CONTROL_WHITESPACE:
        return True
    return unicodedata.category(ch) in ("Zs", "Zl", "Zp") and ch not in _NON_BREAKING_SPACES


def java_strip(text: str) -> str:
    """String.strip() 과 같다."""
    start, end = 0, len(text)
    while start < end and is_java_whitespace(text[start]):
        start += 1
    while end > start and is_java_whitespace(text[end - 1]):
        end -= 1
    return text[start:end]


def java_trim(text: str) -> str:
    """String.trim() 과 같다. U+0020 이하 문자만 지운다(strip 과 다르다)."""
    start, end = 0, len(text)
    while start < end and text[start] <= " ":
        start += 1
    while end > start and text[end - 1] <= " ":
        end -= 1
    return text[start:end]


def java_is_blank(text: str) -> bool:
    """String.isBlank() 와 같다."""
    return all(is_java_whitespace(ch) for ch in text)


def utf16_units(text: str) -> tuple[int, ...]:
    """Java 의 char 배열. BMP 밖 문자는 서로게이트 2개가 된다."""
    data = text.encode("utf-16-le", "surrogatepass")
    return tuple(unit for (unit,) in struct.iter_unpack("<H", data))


def estimate_tokens(text: str | None) -> int:
    """TokenEstimator.estimate 와 같다. 한글·자모·한자 0.7, 그 외 1/4, UTF-16 코드 단위로 센다."""
    if not text:
        return 0
    cjk = other = 0
    for unit in utf16_units(text):
        if 0xAC00 <= unit <= 0xD7A3 or 0x3130 <= unit <= 0x318F or 0x4E00 <= unit <= 0x9FFF:
            cjk += 1
        else:
            other += 1
    return math.ceil(cjk * 0.7 + other / 4.0)

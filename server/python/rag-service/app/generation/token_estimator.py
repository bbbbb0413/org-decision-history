"""토큰 수 추정. 색인과 같은 규칙을 history_shared.text 에서 가져온다.

한글은 글자당 약 0.7토큰, 영문은 4글자당 약 1토큰으로 잡는다 — 정확한 토큰 수가
아니라 "너무 길지 않은가" 판정이므로 추정으로 충분하다. 글자는 UTF-16 단위로 센다(색인 계약 §6).
"""
from history_shared.text import estimate_tokens as estimate

__all__ = ["estimate"]

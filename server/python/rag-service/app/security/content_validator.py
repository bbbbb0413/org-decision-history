from dataclasses import dataclass
import re
import unicodedata

from app.security.rule_loader import Rule, load_rules

_ZERO_WIDTH_PATTERN = re.compile(r"[​-‍﻿]")


@dataclass(frozen=True)
class GuardrailVerdict:
    is_allowed: bool
    reason: str | None = None
    pattern: str | None = None
    # 걸린 규칙(rules/injection.yaml 의 id). 차단 로그와 규칙 파일을 대조할 때 쓴다
    rule_id: str | None = None

    @classmethod
    def allow(cls) -> "GuardrailVerdict":
        return cls(is_allowed=True)

    @classmethod
    def block(cls, reason: str, pattern: str, rule_id: str | None = None) -> "GuardrailVerdict":
        return cls(is_allowed=False, reason=reason, pattern=pattern, rule_id=rule_id)


class ContentValidator:
    """프롬프트 인젝션, 제로너비 공백 우회 및 악의적 패턴을 사전 차단한다. 규칙은 rules/injection.yaml 이다."""

    def __init__(self, rules: list[Rule] | None = None):
        self._rules = rules if rules is not None else load_rules("injection")

    def inspect(self, text: str) -> GuardrailVerdict:
        cleaned = self._clean_text(text)
        matched = next((r for r in self._rules if r.pattern.search(cleaned)), None)
        if matched is None:
            return GuardrailVerdict.allow()
        return GuardrailVerdict.block("의심스러운 지시문 패턴 감지", matched.pattern.pattern, matched.id)

    @staticmethod
    def _clean_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        return _ZERO_WIDTH_PATTERN.sub("", normalized)


__all__ = ["GuardrailVerdict", "ContentValidator"]

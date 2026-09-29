from dataclasses import dataclass
import re
import unicodedata

_ZERO_WIDTH_PATTERN = re.compile(r"[\u200b-\u200d\ufeff]")

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|all)\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"당신의\s*이전\s*지시(사항)?을?\s*무시(해|하세요|하십시오)?", re.IGNORECASE),
    re.compile(r"시스템\s*프롬프트를?\s*(출력|알려줘|보여줘)", re.IGNORECASE),
    re.compile(r"<\s*script[^>]*>", re.IGNORECASE),
]


@dataclass(frozen=True)
class GuardrailVerdict:
    is_allowed: bool
    reason: str | None = None
    pattern: str | None = None

    @classmethod
    def allow(cls) -> "GuardrailVerdict":
        return cls(is_allowed=True)

    @classmethod
    def block(cls, reason: str, pattern: str) -> "GuardrailVerdict":
        return cls(is_allowed=False, reason=reason, pattern=pattern)


class ContentValidator:
    """프롬프트 인젝션, 제로너비 공백 우회 및 악의적 패턴을 사전 차단한다."""

    def inspect(self, text: str) -> GuardrailVerdict:
        cleaned = self._clean_text(text)
        matched = next((p for p in INJECTION_PATTERNS if p.search(cleaned)), None)
        if matched is None:
            return GuardrailVerdict.allow()
        return GuardrailVerdict.block("의심스러운 지시문 패턴 감지", matched.pattern)

    @staticmethod
    def _clean_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        return _ZERO_WIDTH_PATTERN.sub("", normalized)


__all__ = ["INJECTION_PATTERNS", "GuardrailVerdict", "ContentValidator"]

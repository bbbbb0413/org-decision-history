import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    label: str
    pattern: re.Pattern[str]


SECRET_PII_PATTERNS = [
    SecretPattern("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b")),
    SecretPattern("OPENAI_KEY", re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b")),
    SecretPattern("JWT_TOKEN", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]+\b")),
    SecretPattern("KR_RRN", re.compile(r"\b\d{6}-[1-4]\d{6}\b")),
    SecretPattern("CREDIT_CARD", re.compile(r"\b(?:\d{4}-){3}\d{4}\b|\b(?:\d{4}\s){3}\d{4}\b")),
    SecretPattern("KR_PHONE", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")),
    SecretPattern("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
]


class SecretPiiScanner:
    """사내 질문 및 검색 결과에서 개인정보 및 보안 토큰을 마스킹한다."""

    def mask(self, text: str) -> str:
        masked = text
        for entry in SECRET_PII_PATTERNS:
            masked = entry.pattern.sub(f"[REDACTED_{entry.label}]", masked)
        return masked

    def contains_sensitive_data(self, text: str) -> bool:
        return any(entry.pattern.search(text) for entry in SECRET_PII_PATTERNS)


__all__ = ["SECRET_PII_PATTERNS", "SecretPattern", "SecretPiiScanner"]

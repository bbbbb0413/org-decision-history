from app.security.rule_loader import Rule, load_rules


class SecretPiiScanner:
    """사내 질문 및 검색 결과에서 개인정보 및 보안 토큰을 마스킹한다. 규칙은 rules/secrets.yaml 이다."""

    def __init__(self, rules: list[Rule] | None = None):
        self._rules = rules if rules is not None else load_rules("secrets")

    def mask(self, text: str) -> str:
        return self.scan(text)[0]

    def scan(self, text: str) -> tuple[str, list[str]]:
        """마스킹한 문자열과 걸린 규칙 id 목록. 로그에는 id 만 남긴다 — 값을 남기면 마스킹한 뜻이 없다."""
        masked, hit = text, []
        for rule in self._rules:
            masked, count = rule.pattern.subn(f"[REDACTED_{rule.id}]", masked)
            if count:
                hit.append(rule.id)
        return masked, hit

    def contains_sensitive_data(self, text: str) -> bool:
        return any(rule.pattern.search(text) for rule in self._rules)


__all__ = ["SecretPiiScanner"]

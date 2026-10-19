"""보안 규칙 파일의 예시를 전부 돌린다. 정규식을 고치다 오탐·미탐이 생기면 여기서 잡힌다."""
import pytest

from app.security.content_validator import ContentValidator
from app.security.pii_scanner import SecretPiiScanner
from app.security.rule_loader import load_rules

INJECTION = load_rules("injection")
SECRETS = load_rules("secrets")


@pytest.mark.parametrize("rule", INJECTION, ids=lambda r: r.id)
def test_injection_rule_examples(rule):
    validator = ContentValidator()
    assert rule.should_match and rule.should_not_match, "규칙마다 두 종류 예시를 모두 둔다"
    for text in rule.should_match:
        verdict = validator.inspect(text)
        assert not verdict.is_allowed and verdict.rule_id == rule.id, text
    # 걸리면 안 되는 예는 규칙 전체로 본다 — 다른 규칙에 걸려도 오탐이다
    for text in rule.should_not_match:
        assert validator.inspect(text).is_allowed, text


@pytest.mark.parametrize("rule", SECRETS, ids=lambda r: r.id)
def test_secret_rule_examples(rule):
    scanner = SecretPiiScanner([rule])
    assert rule.should_match and rule.should_not_match, "규칙마다 두 종류 예시를 모두 둔다"
    for text in rule.should_match:
        masked, hit = scanner.scan(text)
        assert hit == [rule.id] and f"[REDACTED_{rule.id}]" in masked, text
    for text in rule.should_not_match:
        assert scanner.scan(text) == (text, []), text


def test_rule_ids_are_unique_and_masking_labels_unchanged():
    # 마스킹 문자열([REDACTED_<id>])은 프롬프트·저장된 대화에 남는 값이라 바꾸지 않는다
    assert [r.id for r in SECRETS] == [
        "AWS_KEY", "GITHUB_TOKEN", "OPENAI_KEY", "JWT_TOKEN", "KR_RRN", "CREDIT_CARD", "KR_PHONE", "EMAIL",
    ]


def test_override_directory_and_broken_rule_fail_loudly(tmp_path):
    (tmp_path / "injection.yaml").write_text(
        "rules:\n  - id: only\n    pattern: 'forbidden'\n", encoding="utf-8")
    assert [r.id for r in load_rules("injection", tmp_path)] == ["only"]

    (tmp_path / "secrets.yaml").write_text("rules:\n  - id: bad\n    pattern: '('\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules("secrets", tmp_path)
    with pytest.raises(FileNotFoundError):
        load_rules("missing", tmp_path)

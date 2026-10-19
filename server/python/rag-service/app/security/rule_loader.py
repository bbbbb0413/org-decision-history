"""보안 규칙 파일(rules/*.yaml) 읽기.

규칙은 코드가 아니라 데이터다 — WAF 규칙처럼 목록으로 관리하고, 운영 중 패턴을 더하려면 이미지를 다시 굽지 않고
설정 SECURITY_RULES_PATH 로 디렉터리를 바꿔 끼운다. 파일이 없거나 정규식이 틀리면 기동할 때 실패한다(조용히 규칙 없이 돌지 않는다).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_DIR = Path(__file__).parent / "rules"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    description: str = ""
    should_match: tuple[str, ...] = field(default=())
    should_not_match: tuple[str, ...] = field(default=())


def load_rules(name: str, directory: str | Path | None = None) -> list[Rule]:
    """directory 가 비어 있으면 이미지에 든 기본 규칙(app/security/rules)을 읽는다."""
    path = (Path(directory) if directory else DEFAULT_DIR) / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules: list[Rule] = []
    seen: set[str] = set()
    for raw in data.get("rules") or []:
        rule_id = str(raw["id"])
        if rule_id in seen:
            raise ValueError(f"{path}: 규칙 id 가 겹친다: {rule_id}")
        seen.add(rule_id)
        flags = re.IGNORECASE if raw.get("ignore_case") else 0
        try:
            pattern = re.compile(raw["pattern"], flags)
        except re.error as e:
            raise ValueError(f"{path}: 규칙 {rule_id} 의 정규식이 틀렸다: {e}") from e
        rules.append(Rule(
            id=rule_id,
            pattern=pattern,
            description=str(raw.get("description", "")),
            should_match=tuple(raw.get("should_match") or ()),
            should_not_match=tuple(raw.get("should_not_match") or ()),
        ))
    if not rules:
        raise ValueError(f"{path}: 규칙이 하나도 없다")
    return rules

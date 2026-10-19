"""rag-service 프롬프트 파일(app/prompts/*.md). 읽는 규칙은 history_shared.prompts 에 있다."""
from pathlib import Path

from history_shared.prompts import load_prompt

PROMPTS_DIR = Path(__file__).parent / "prompts"


def prompt(name: str, **values: object) -> str:
    return load_prompt(PROMPTS_DIR, name, **values)

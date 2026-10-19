"""프롬프트 파일(*.md) 읽기.

프롬프트는 코드와 같은 저장소에 두고 이미지에 포함한다. 런타임에 바꿔 끼우지 않는다 — 프롬프트는 응답을
해석하는 코드(JSON 키·줄 형식)와 짝이라 따로 바뀌면 깨진다. 파일로 빼는 목적은 diff·리뷰를 쉽게 하는 것이다.

파일 맨 앞의 HTML 주석(<!-- ... -->)은 사람을 위한 설명이라 프롬프트에서 뺀다. 파일 끝 줄바꿈 하나도 뺀다
(편집기가 붙이는 것이라서다 — 프롬프트가 줄바꿈으로 끝나야 하면 빈 줄을 하나 더 둔다).
{이름} 자리표시자는 load_prompt(..., 이름=값) 으로 채운다. 값을 주지 않으면 중괄호를 그대로 둔다
(JSON 예시가 든 프롬프트가 있어서다).
"""
import re
from pathlib import Path

_LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*\n", re.DOTALL)


def load_prompt(directory: Path, name: str, **values: object) -> str:
    text = (directory / f"{name}.md").read_text(encoding="utf-8")
    text = _LEADING_COMMENT.sub("", text, count=1)
    if text.endswith("\n"):
        text = text[:-1]
    if values:
        for key, value in values.items():
            placeholder = "{" + key + "}"
            if placeholder not in text:
                raise ValueError(f"{name}.md 에 자리표시자 {placeholder} 가 없다")
            text = text.replace(placeholder, str(value))
    return text

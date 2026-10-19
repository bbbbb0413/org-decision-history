"""근거를 바탕으로 답변을 만든다."""
from typing import Awaitable, Callable, Protocol

from app.generation.models import AnswerChunk, AnswerRequest

OnChunk = Callable[[AnswerChunk], Awaitable[None]]


class AnswerGenerator(Protocol):
    async def generate(self, request: AnswerRequest, on_chunk: OnChunk) -> None:
        """답변을 조각 단위로 흘려보낸다. 마지막에 done 조각이 온다."""
        ...

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """대화 요약·질문 재작성·질의 확장·자가 비판에 쓰는 경량 호출.

        LLM 이 없거나 호출이 실패하거나 본문이 비면 `user_prompt` 를 그대로 돌려준다. 호출부는 지시문을
        `system_prompt` 에 두고, 결과가 `user_prompt` 와 같으면 실패로 판별한다.
        """
        ...

"""근거를 바탕으로 답변을 만든다."""
from typing import Awaitable, Callable, Protocol

from app.generation.models import AnswerChunk, AnswerRequest

OnChunk = Callable[[AnswerChunk], Awaitable[None]]


class AnswerGenerator(Protocol):
    async def generate(self, request: AnswerRequest, on_chunk: OnChunk) -> None:
        """답변을 조각 단위로 흘려보낸다. 마지막에 done 조각이 온다."""
        ...

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """대화 요약과 질문 재작성에 쓰는 경량 호출."""
        ...

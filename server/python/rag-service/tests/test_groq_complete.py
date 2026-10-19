"""GroqAnswerGenerator.complete — 경량 호출의 추론 강도와 빈 본문 처리."""
from types import SimpleNamespace

import pytest
from app.generation.groq_client import GroqAnswerGenerator
from app.settings import Settings


class FakeCompletions:
    def __init__(self, content: str | None, finish_reason: str = "stop"):
        self._content = content
        self._finish_reason = finish_reason
        self.kwargs: dict = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=self._finish_reason)])


def _generator(content: str | None, finish_reason: str = "stop", **settings) -> tuple[GroqAnswerGenerator, FakeCompletions]:
    gen = GroqAnswerGenerator(Settings(groq_api_key="dummy", **settings))
    completions = FakeCompletions(content, finish_reason)
    gen._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return gen, completions


@pytest.mark.asyncio
async def test_complete_lowers_reasoning_effort():
    """추론 모델은 추론 토큰도 max_tokens 에서 깎인다. 낮추지 않으면 200 토큰 예산에서 본문이 빈다."""
    gen, completions = _generator("결과")
    assert await gen.complete("지시", "질문", 200) == "결과"
    assert completions.kwargs["extra_body"] == {"reasoning_effort": "low"}
    assert completions.kwargs["model"] == Settings().fallback_model


@pytest.mark.asyncio
async def test_complete_can_disable_reasoning_effort():
    """추론 모델이 아니면 인자를 보내지 않는다."""
    gen, completions = _generator("결과", light_reasoning_effort="")
    await gen.complete("지시", "질문", 200)
    assert completions.kwargs["extra_body"] is None


@pytest.mark.asyncio
async def test_complete_rejection_does_not_trip_breaker():
    """400(tool_use_failed 등)은 서버 장애가 아니다. 서킷에 세면 본 답변 생성까지 막힌다."""
    import groq
    import httpx

    gen, completions = _generator("unused")
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = groq.BadRequestError("tool_use_failed", response=httpx.Response(400, request=request), body=None)

    async def reject(**kwargs):
        raise error

    completions.create = reject
    for _ in range(10):
        assert await gen.complete("지시", "질문", 200) == "질문"
    assert gen._breaker.can_call()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "", "  \n"])
async def test_complete_echoes_input_on_empty_body(content):
    """본문 없이 끝나면(예산 소진) 실패와 같게 입력을 돌려준다 — 호출부가 원래 값으로 떨어진다."""
    gen, _ = _generator(content, finish_reason="length")
    assert await gen.complete("지시", "질문", 200) == "질문"

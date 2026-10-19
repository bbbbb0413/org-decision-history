"""LLM 호출 실패 분류. 한도 초과·서버 오류는 재시도할 실패로, 잘못된 요청은 그대로 올린다."""
import httpx
import pytest
from history_shared.llm import LlmUnavailableError

from app.generation import AnswerGenerator
from app.settings import Settings


def _generator(statuses: list[int]) -> tuple[AnswerGenerator, list[str]]:
    models: list[str] = []
    replies = iter(statuses)

    def handle(request: httpx.Request) -> httpx.Response:
        import json
        models.append(json.loads(request.content)["model"])
        status = next(replies)
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "답"}}]})
        return httpx.Response(status, json={"error": "x"})

    generator = AnswerGenerator(Settings(groq_api_key="k", primary_model="big", fallback_model="small"))
    generator._client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return generator, models


async def test_rate_limited_on_both_models_is_unavailable():
    generator, models = _generator([429, 429])
    with pytest.raises(LlmUnavailableError):
        await generator.complete("s", "u")
    assert models == ["big", "small"]


async def test_server_error_on_fallback_is_unavailable():
    generator, _ = _generator([429, 503])
    with pytest.raises(LlmUnavailableError):
        await generator.complete("s", "u")


async def test_bad_request_is_not_treated_as_temporary():
    generator, _ = _generator([400, 400])
    with pytest.raises(httpx.HTTPStatusError):
        await generator.complete("s", "u")


async def test_fallback_model_answers_when_primary_is_rate_limited():
    generator, models = _generator([429, 200])
    assert await generator.complete("s", "u") == "답"
    assert models == ["big", "small"]

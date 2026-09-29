"""LLM 호출 생성기 (Groq)."""
import time

import httpx
import structlog
from history_shared.metrics import LLM_REQUEST_SECONDS

from app.settings import Settings

log = structlog.get_logger()


class AnswerGenerator:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._api_key = settings.groq_api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(self, system: str, user: str, max_tokens: int = 1500) -> str:
        if not self._api_key:
            return ""

        url = f"{self._settings.groq_endpoint.rstrip('/')}/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._settings.primary_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        try:
            return await self._post(url, headers, body)
        except Exception as e:
            log.warning("Groq 기본 모델 호출 실패, fallback 시도", error=str(e))
            if self._settings.fallback_model:
                body["model"] = self._settings.fallback_model
                return await self._post(url, headers, body)
            raise

    async def _post(self, url: str, headers: dict, body: dict) -> str:
        """호출 하나의 지연·성패를 메트릭으로 남긴다.

        모델별로 나눠 재는 게 중요하다 — fallback 모델로 넘어갔을 때 지연이 어떻게
        달라지는지가 기본 모델 한도 초과를 판단하는 근거가 된다.
        """
        model = body.get("model", "unknown")
        started = time.monotonic()
        outcome = "error"
        try:
            resp = await self._client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            outcome = "success"
            return content
        finally:
            LLM_REQUEST_SECONDS.labels(model=model, outcome=outcome).observe(time.monotonic() - started)

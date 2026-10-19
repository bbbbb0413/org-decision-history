"""Groq 스트리밍 답변 생성 및 서킷 브레이커 장애 격리."""
import time
from datetime import datetime, timedelta, timezone

import groq
import structlog
from history_shared.metrics import LLM_REQUEST_SECONDS

from app.generation.base import OnChunk
from app.generation.circuit_breaker import CircuitBreaker, CircuitBreakerOpenException
from app.generation.models import AnswerChunk, AnswerRequest, AnswerStatus
from app.generation.prompt import SYSTEM_PROMPT, build_user_prompt, trim_evidence
from app.settings import Settings

log = structlog.get_logger()


class RateLimitedError(Exception):
    """한도 초과. 폴백 판단에만 쓴다."""

    def __init__(self, retry_after_seconds: float | None):
        super().__init__()
        self.retry_after_seconds = retry_after_seconds


class GroqAnswerGenerator:
    def __init__(self, settings: Settings, breaker: CircuitBreaker | None = None):
        self._settings = settings
        self._client = groq.AsyncGroq(api_key=settings.groq_api_key, base_url=settings.groq_endpoint)
        self._breaker = breaker or CircuitBreaker(failure_threshold=5, reset_timeout_seconds=30.0)
        self._budget = settings.prompt_budget()

    async def generate(self, request: AnswerRequest, on_chunk: OnChunk) -> None:
        evidence = trim_evidence(request.evidence, self._budget)
        if not evidence:
            await on_chunk(AnswerChunk.finished(AnswerStatus.NO_EVIDENCE, self._settings.primary_model))
            return
        user_prompt = build_user_prompt(
            request.question, request.summary, request.recent_turns, evidence, self._budget
        )

        try:
            await self._stream(self._settings.primary_model, user_prompt, on_chunk)
            await on_chunk(AnswerChunk.finished(AnswerStatus.OK, self._settings.primary_model))
        except CircuitBreakerOpenException:
            log.warning("서킷 브레이커 차단으로 인한 Fast-Fail 처리")
            await on_chunk(
                AnswerChunk.delta("현재 AI 서비스 연결 장애로 인해 일시적으로 답변 생성이 제한됩니다. 잠시 후 다시 시도해 주세요.\n")
            )
            await on_chunk(AnswerChunk.finished(AnswerStatus.DEGRADED, "circuit_breaker"))
        except RateLimitedError:
            log.warning("기본 모델 한도 초과. 경량 모델로 전환한다")
            try:
                await self._stream(self._settings.fallback_model, user_prompt, on_chunk)
                await on_chunk(AnswerChunk.finished(AnswerStatus.DEGRADED, self._settings.fallback_model))
            except RateLimitedError as both_limited:
                resume_at = None
                if both_limited.retry_after_seconds is not None:
                    resume_at = datetime.now(timezone.utc) + timedelta(seconds=both_limited.retry_after_seconds)
                await on_chunk(
                    AnswerChunk.finished(AnswerStatus.RATE_LIMITED, self._settings.fallback_model, resume_at)
                )

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        if not self._breaker.can_call():
            log.warning("서킷 브레이커 OPEN 상태로 요약/재작성 생략 (원본 유지)")
            return user_prompt

        started = time.monotonic()
        outcome = "error"
        # gpt-oss 는 추론 모델이라 보이지 않는 추론 토큰도 max_tokens 에서 깎인다. 기본(medium) 추론이면
        # 200 토큰 예산을 추론이 다 써서 본문이 비어 돌아온다. 경량 호출은 추론을 낮춘다.
        # groq 0.13 SDK 에는 이 인자가 없어 extra_body 로 보낸다
        extra_body = (
            {"reasoning_effort": self._settings.light_reasoning_effort}
            if self._settings.light_reasoning_effort
            else None
        )
        try:
            resp = await self._client.chat.completions.create(
                model=self._settings.fallback_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=self._settings.light_temperature,
                extra_body=extra_body,
            )
            self._breaker.record_success()
            outcome = "success"
            choice = resp.choices[0]
            content = choice.message.content or ""
            if not content.strip():
                # 본문 없이 끝났다(대개 예산 소진). 실패와 같게 입력을 돌려줘 호출부가 원래 값으로 떨어지게 한다
                log.warning("경량 모델이 빈 본문을 돌려줬다. 원문을 그대로 쓴다", finish_reason=choice.finish_reason)
                return user_prompt
            return content
        except (groq.BadRequestError, groq.RateLimitError) as e:
            # 400(gpt-oss 가 가끔 내는 tool_use_failed 등)과 429 는 서버 장애가 아니다. 서킷에 세면
            # 경량 호출의 일시 오류가 쌓여 본 답변 생성까지 차단된다
            outcome = "rejected"
            log.warning("경량 모델 요청이 거절됐다. 원문을 그대로 쓴다", error=str(e)[:200])
            return user_prompt
        except Exception as e:
            self._breaker.record_failure(e)
            log.warning("경량 모델 호출 실패. 원문을 그대로 쓴다", error=str(e))
            return user_prompt
        finally:
            _observe(self._settings.fallback_model, outcome, started)

    async def _stream(self, model: str, user_prompt: str, on_chunk: OnChunk) -> None:
        if not self._breaker.can_call():
            raise CircuitBreakerOpenException("서킷 브레이커 OPEN")

        # 스트리밍이라 "첫 토큰까지"가 아니라 마지막 토큰까지의 전체 시간을 잰다 —
        # 사용자가 답을 다 받기까지 걸린 시간이 이 지표로 보고 싶은 값이다
        started = time.monotonic()
        outcome = "error"
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=self._settings.max_output_tokens,
                temperature=0.2,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    await on_chunk(AnswerChunk.delta(delta))
            self._breaker.record_success()
            outcome = "success"
        except groq.RateLimitError as e:
            # 429 RateLimit은 인프라 서버 장애가 아니므로 서킷 차단 카운트에는 넣지 않는다
            outcome = "rate_limited"
            retry_after = e.response.headers.get("retry-after") if e.response is not None else None
            raise RateLimitedError(_parse_retry_after(retry_after)) from e
        except Exception as e:
            self._breaker.record_failure(e)
            raise
        finally:
            _observe(model, outcome, started)


def _observe(model: str, outcome: str, started: float) -> None:
    LLM_REQUEST_SECONDS.labels(model=model, outcome=outcome).observe(time.monotonic() - started)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None

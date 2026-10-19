"""근거 충분성 판정 — 검색된 근거에 질문의 답이 실제로 있는가.

낱말 겹침(relevance_gate)은 값싼 1차 거름이다. 근거 5건의 낱말을 합쳐 세므로 "방화벽 규칙 변경 승인 절차"처럼
흔한 낱말('변경'·'승인'·'절차')이 여러 청크에 흩어져 있으면 답이 없는데도 통과한다. 리랭커 점수도 같은 구간에
"답은 있지만 확신이 낮은 질문"과 "낱말이 겹치지만 답이 없는 질문"이 섞여 있어 기준값 하나로 가를 수 없다
(evaluation.retrieval_eval 로 잰다).

그래서 벡터 유사도가 아주 낮은 경우(바로 거절)를 빼고는 경량 모델에게 묻는다(graph judge_evidence). 답변 생성과 다른
모델(fallback_model)을 쓰는 경량 호출(`complete`)이라 생성 쪽 한도를 깎지 않는다. 다만 HyDE·질문 분해·자가비판과
같은 모델의 한도(Groq 무료: 분당 8,000토큰)를 나눠 쓴다.
판정하지 못하면(키 없음·한도 초과·파싱 실패) evaluated=False 를 돌려주고, 호출부가 낱말·점수 1차 거름으로 정한다.

프롬프트의 예시는 평가셋(evaluation/datasets)과 겹치지 않게 둔다 — 겹치면 평가가 답을 미리 본 셈이 된다.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from history_shared.metrics import EVIDENCE_JUDGE_CACHE

from app.search.models import SearchHit
from app.prompt_files import prompt

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator

log = structlog.get_logger()

# 본문은 prompts/evidence_judge.md
JUDGE_SYSTEM = prompt("evidence_judge")
# gpt-oss 는 추론 토큰도 max_tokens 에서 깎인다. 낮은 추론으로 JSON 한 줄이면 이 정도로 충분하다
MAX_JUDGE_TOKENS = 200
# 청크 머리글: "[문서] 제목 | 작성일 2026-05-18 | 절 제목 > 절\n\n본문"
_HEADER = re.compile(r"^\[문서\] (?P<title>[^|\n]*?)\s*(?:\|[^\n]*)?\n\n", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Judgement:
    answerable: bool
    # False 면 모델이 판정하지 않았다(키 없음·한도 초과·파싱 실패). 호출부가 1차 거름으로 정한다
    evaluated: bool = True

    @classmethod
    def unknown(cls) -> "Judgement":
        return cls(answerable=True, evaluated=False)


class JudgeCache:
    """판정 결과 캐시. 키는 판정 프롬프트·질문·근거 청크(문서·판·청크 번호)다.

    문서가 개정되면 판 번호가 바뀌어 키도 바뀐다. 결과는 그 청크들에 대한 참/거짓뿐이고, 권한 범위가 다르면
    검색되는 근거가 달라 키가 달라지므로 범위끼리 판정을 공유하지 않는다. Redis 오류는 캐시 없음으로 취급한다.
    """

    def __init__(self, redis_client, ttl_seconds: int = 86_400):
        self._redis = redis_client
        self._ttl = ttl_seconds

    async def get(self, prompt_id: str, question: str, evidence: list[SearchHit]) -> bool | None:
        try:
            value = await self._redis.get(self._key(prompt_id, question, evidence))
        except Exception as e:  # noqa: BLE001 — 캐시 장애가 판정을 막지 않는다
            log.debug("판정 캐시 읽기 실패", error=str(e))
            return None
        if value is None:
            return None
        return str(value) == "1"

    async def put(self, prompt_id: str, question: str, evidence: list[SearchHit], answerable: bool) -> None:
        try:
            await self._redis.set(self._key(prompt_id, question, evidence), "1" if answerable else "0", ex=self._ttl)
        except Exception as e:  # noqa: BLE001
            log.debug("판정 캐시 쓰기 실패", error=str(e))

    @staticmethod
    def _key(prompt_id: str, question: str, evidence: list[SearchHit]) -> str:
        # 프롬프트가 바뀌면 판정 기준이 바뀐 것이다 — 옛 판정을 쓰지 않는다
        normalized = _WHITESPACE.sub(" ", question.lower()).strip()
        raw = prompt_id + "\n" + normalized + "\n" + ",".join(h.key for h in evidence)
        return "evidence_judge:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def compact_evidence(hit: SearchHit, max_chars: int) -> str:
    """판정용으로 근거를 줄인다. 머리글은 "제목 > 절" 하나로, 본문은 앞 max_chars 자만 남긴다."""
    content = hit.content or ""
    match = _HEADER.match(content)
    if match:
        label = hit.section_path or match.group("title").strip()
        body = content[match.end():]
    else:
        label, body = hit.section_path or "", content
    body = _WHITESPACE.sub(" ", body).strip()[:max_chars]
    return f"({label}) {body}" if label else body


class EvidenceJudge:
    def __init__(
        self,
        generator: AnswerGenerator,
        *,
        max_evidence: int = 3,
        max_chars: int = 400,
        cache: JudgeCache | None = None,
        system_prompt: str | None = None,
    ):
        self._generator = generator
        # 판정 한 번의 토큰을 줄인다(Groq 무료: 경량 모델 하루 200,000토큰). 근거 3건 × 400자면 약 500토큰이다.
        # 정답 청크는 재순위화 후 대개 1~3위에 있다(평가셋)
        self._max_evidence = max_evidence
        self._max_chars = max_chars
        self._cache = cache
        self._system_prompt = system_prompt or JUDGE_SYSTEM
        # 판정 기준(프롬프트·근거 축약 방식)이 바뀌면 캐시 키도 바뀐다
        self._prompt_id = hashlib.sha256(
            f"{self._system_prompt}|{max_evidence}|{max_chars}".encode("utf-8")
        ).hexdigest()[:12]

    async def judge(self, question: str, evidence: list[SearchHit]) -> Judgement:
        if not evidence:
            return Judgement(answerable=False)
        shown = evidence[: self._max_evidence]
        if self._cache is not None:
            cached = await self._cache.get(self._prompt_id, question, shown)
            EVIDENCE_JUDGE_CACHE.labels(result="hit" if cached is not None else "miss").inc()
            if cached is not None:
                return Judgement(answerable=cached)
        context = "\n".join(f"[{i + 1}] {compact_evidence(h, self._max_chars)}" for i, h in enumerate(shown))
        prompt = f"질문: {question}\n\n근거:\n{context}"
        try:
            raw = (await self._generator.complete(self._system_prompt, prompt, MAX_JUDGE_TOKENS)).strip()
        except Exception as e:  # noqa: BLE001 — 판정 실패가 답변을 막지 않는다
            log.warning("근거 충분성 판정 실패", error=str(e))
            return Judgement.unknown()
        # complete 는 LLM 이 없거나 실패(한도 초과 포함)하면 입력을 그대로 돌려준다
        if raw == prompt.strip():
            return Judgement.unknown()
        judgement = _parse(raw)
        # 판정한 것만 캐시한다. 실패를 캐시하면 한도가 풀린 뒤에도 판정하지 않는다
        if judgement.evaluated and self._cache is not None:
            await self._cache.put(self._prompt_id, question, shown, judgement.answerable)
        return judgement


def _parse(raw: str) -> Judgement:
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not match:
        log.warning("근거 충분성 판정 응답에 JSON 이 없다 (통과로 둔다)", raw=raw[:100])
        return Judgement.unknown()
    try:
        value = json.loads(match.group(0)).get("answerable")
    except (ValueError, AttributeError):
        log.warning("근거 충분성 판정 JSON 파싱 실패 (통과로 둔다)", raw=raw[:100])
        return Judgement.unknown()
    # 문자열 "false" 를 참으로 받지 않는다 — 불리언만 판정으로 인정한다
    if not isinstance(value, bool):
        return Judgement.unknown()
    return Judgement(answerable=value)


__all__ = ["EvidenceJudge", "JudgeCache", "Judgement", "compact_evidence"]

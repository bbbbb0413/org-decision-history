"""검색 질의 계획. 경량 모델 한 번으로 질문 재작성, 검색 갈래(simple/complex/abstract), 하위 질의를 함께 받는다.

키워드 사전으로 갈래를 추측하던 라우터를 대신한다. complex(하위 질의로 다중 검색)와 abstract(HyDE)는 어차피 LLM 이
있어야 쓸모가 있으므로, LLM 이 없거나 실패하면(응답이 입력 그대로·JSON 아님·형식이 어긋남) simple 로 떨어진다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

from app.prompt_files import prompt

if TYPE_CHECKING:
    from app.generation.base import AnswerGenerator

log = structlog.get_logger()

MAX_SUB_QUERIES = 3
# 경량 모델은 추론 토큰도 max_tokens 에서 깎인다(reasoning_effort low). JSON 본문은 100토큰 안팎이다
MAX_PLAN_TOKENS = 300
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

# 본문은 prompts/query_plan.md
QUERY_PLAN_SYSTEM = prompt("query_plan", max_sub_queries=MAX_SUB_QUERIES)

Kind = Literal["simple", "complex", "abstract"]


@dataclass(frozen=True)
class QueryPlan:
    # 검색에 쓸 독립형 질의. 대화 맥락이 없으면 원래 질문 그대로다
    query: str
    kind: Kind = "simple"
    # complex 일 때만 2개 이상
    sub_queries: tuple[str, ...] = ()


class QueryPlanner:
    def __init__(self, generator: AnswerGenerator, max_sub_queries: int = MAX_SUB_QUERIES, system_prompt: str | None = None):
        self._generator = generator
        self._max_sub_queries = max_sub_queries
        # 평가(evaluation.plan_eval --prompt)에서 후보 프롬프트를 비교할 때만 바꾼다
        self._system_prompt = system_prompt or QUERY_PLAN_SYSTEM

    async def plan(self, question: str, context: str | None = None) -> QueryPlan:
        """context 는 [이전 요약]·[최근 대화] 블록(ConversationMemory.context_of). 없으면 첫 질문이다."""
        user_prompt = f"{context}\n\n[현재 질문]\n{question}" if context else f"[현재 질문]\n{question}"
        try:
            raw = (await self._generator.complete(self._system_prompt, user_prompt, MAX_PLAN_TOKENS)).strip()
        except Exception as e:  # noqa: BLE001 — 계획 실패가 검색을 막지 않는다
            log.warning("검색 질의 계획 실패 (원래 질문으로 검색)", error=str(e))
            return QueryPlan(question)
        # complete 는 LLM 이 없거나 실패하면 입력을 그대로 돌려준다
        if raw == user_prompt.strip():
            return QueryPlan(question)
        return self._parse(raw, question, has_context=bool(context))

    def _parse(self, raw: str, question: str, has_context: bool) -> QueryPlan:
        match = _JSON_OBJECT.search(raw)
        try:
            data = json.loads(match.group(0)) if match else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            log.warning("검색 질의 계획 응답을 해석하지 못함 (원래 질문으로 검색)", raw=raw[:100])
            return QueryPlan(question)

        # 맥락이 없으면 바꿔 쓸 이유가 없다 — 모델이 고친 문장보다 사용자 원문이 검색에 낫다.
        # 맥락이 있어도 재작성이 비었거나 지나치게 길면 원문을 쓴다(옛 재작성과 같은 규칙)
        query = data.get("query")
        if not has_context or not isinstance(query, str) or not query.strip() or len(query) > len(question) * 6:
            query = question
        query = query.strip()

        kind = data.get("kind")
        if kind == "complex":
            subs = data.get("sub_queries")
            subs = [s.strip() for s in subs if isinstance(s, str) and s.strip()] if isinstance(subs, list) else []
            subs = subs[: self._max_sub_queries]
            # 하나로만 나왔다면 나눈 것이 아니다
            if len(subs) >= 2:
                return QueryPlan(query, "complex", tuple(subs))
            return QueryPlan(query)
        if kind == "abstract":
            return QueryPlan(query, "abstract")
        return QueryPlan(query)


__all__ = ["QueryPlan", "QueryPlanner", "QUERY_PLAN_SYSTEM"]

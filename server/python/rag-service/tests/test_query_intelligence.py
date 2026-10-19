import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.graph.nodes import GraphNodes
from app.search.query_planner import QUERY_PLAN_SYSTEM, QueryPlan, QueryPlanner
from app.search.hyde import HYDE_SYSTEM, HydeService
from app.generation.extractive import ExtractiveAnswerGenerator
from app.generation.groq_client import GroqAnswerGenerator
from app.settings import Settings


class MockGenerator:
    """경량 호출(complete)만 흉내 낸다. generate 를 부르면 실패한다 — 질의 확장은 답변 생성 경로를 타면 안 된다."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[tuple[str, str]] = []

    async def generate(self, request, on_chunk) -> None:
        raise AssertionError("질의 확장이 답변 생성(generate)을 불렀다")

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response_text


class EchoGenerator(MockGenerator):
    """LLM 이 없거나 실패했을 때의 complete — 입력을 그대로 돌려준다."""

    def __init__(self):
        super().__init__("")

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        return user_prompt


def plan_reply(query="인증팀과 결제팀 정책", kind="simple", subs=()):
    return json.dumps({"query": query, "kind": kind, "sub_queries": list(subs)}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_planner_complex_question_gets_sub_queries_in_one_call():
    gen = MockGenerator(plan_reply(kind="complex", subs=["인증팀 정책은?", "결제팀 정책은?"]))
    plan = await QueryPlanner(gen).plan("인증팀과 결제팀 정책 차이점")

    assert plan == QueryPlan("인증팀과 결제팀 정책 차이점", "complex", ("인증팀 정책은?", "결제팀 정책은?"))
    # 지시문은 시스템 프롬프트로, 사용자 프롬프트에는 질문만 들어간다(첫 질문이라 맥락 없음)
    assert gen.calls == [(QUERY_PLAN_SYSTEM, "[현재 질문]\n인증팀과 결제팀 정책 차이점")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, expected",
    [
        # 하나로만 나왔다면 나눈 것이 아니다
        (plan_reply(kind="complex", subs=["결제 정책은?"]), QueryPlan("결제 정책 알려줘")),
        # 하위 질의는 최대 3개
        (plan_reply(kind="complex", subs=["a", "b", "c", "d"]), QueryPlan("결제 정책 알려줘", "complex", ("a", "b", "c"))),
        (plan_reply(kind="abstract"), QueryPlan("결제 정책 알려줘", "abstract")),
        # 모르는 갈래·JSON 아님·LLM 없음(입력 그대로)은 원래 질문 하나로 검색한다
        (plan_reply(kind="hyde"), QueryPlan("결제 정책 알려줘")),
        ("JSON 아님", QueryPlan("결제 정책 알려줘")),
        ("```json\n" + plan_reply(kind="abstract") + "\n```", QueryPlan("결제 정책 알려줘", "abstract")),
    ],
)
async def test_planner_parses_reply(reply, expected):
    assert await QueryPlanner(MockGenerator(reply)).plan("결제 정책 알려줘") == expected


@pytest.mark.asyncio
async def test_planner_keywords_no_longer_decide_the_route():
    """키워드로 고르면 '왜곡'의 '왜', '그리고' 가 든 단순 질문이 복합 질문이 된다. 갈래는 모델 응답이 정한다."""
    for question in ("통계 왜곡을 막는 원칙은?", "출장비 정산 기한 그리고 한도"):
        assert (await QueryPlanner(MockGenerator(plan_reply(kind="simple"))).plan(question)).kind == "simple"


@pytest.mark.asyncio
async def test_planner_falls_back_on_echo_and_error():
    assert await QueryPlanner(EchoGenerator()).plan("결제 정책", "[최근 대화]\nuser: 안녕") == QueryPlan("결제 정책")

    class Failing(MockGenerator):
        async def complete(self, system_prompt, user_prompt, max_tokens):
            raise RuntimeError("down")

    assert await QueryPlanner(Failing("")).plan("결제 정책") == QueryPlan("결제 정책")


@pytest.mark.asyncio
async def test_planner_rewrites_only_with_context():
    reply = plan_reply(query="결제팀 출장비 정산 기한")
    # 첫 질문(맥락 없음)은 모델이 바꿔 써도 원문으로 검색한다
    assert (await QueryPlanner(MockGenerator(reply)).plan("정산 기한은?")).query == "정산 기한은?"

    gen = MockGenerator(reply)
    plan = await QueryPlanner(gen).plan("그거 기한은?", "[최근 대화]\nuser: 결제팀 출장비 정산")
    assert plan.query == "결제팀 출장비 정산 기한"
    assert gen.calls[0][1] == "[최근 대화]\nuser: 결제팀 출장비 정산\n\n[현재 질문]\n그거 기한은?"

    # 재작성이 지나치게 길면 원문
    long = MockGenerator(plan_reply(query="가" * 100))
    assert (await QueryPlanner(long).plan("기한은?", "[최근 대화]\nuser: x")).query == "기한은?"


def make_nodes(generator, planner=None, memory=None):
    db = MagicMock()
    db.append_user_message = AsyncMock()
    db.rename_if_untitled = AsyncMock()
    return GraphNodes(
        db=db, auth=MagicMock(), hybrid_search=MagicMock(), source_resolver=MagicMock(), answer_cache=MagicMock(),
        memory=memory or MagicMock(), generator=generator, query_planner=planner,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind, subs, expected",
    [
        ("simple", (), ["결제 승인 절차"]),
        ("complex", ("인증팀 정책", "결제팀 정책"), ["인증팀 정책", "결제팀 정책"]),
        # abstract 는 [질의, 가상 문서]
        ("abstract", (), ["결제 승인 절차", "가상 문서"]),
    ],
)
async def test_search_queries_follow_plan(kind, subs, expected):
    nodes = make_nodes(MockGenerator("가상 문서"))
    out = await nodes.search_queries_for(QueryPlan("결제 승인 절차", kind, subs))
    assert out == {"complexity": kind, "search_queries": expected}


@pytest.mark.asyncio
async def test_plan_query_node_uses_one_planner_call_and_stores_rewrite():
    gen = MockGenerator(plan_reply(query="결제팀 출장비 정산 기한", kind="simple"))
    memory = MagicMock()
    memory.context_of = AsyncMock(return_value="[최근 대화]\nuser: 결제팀 출장비")
    memory.rewrite_question = AsyncMock(side_effect=AssertionError("계획이 켜져 있으면 재작성을 따로 부르지 않는다"))
    nodes = make_nodes(gen, QueryPlanner(gen), memory)

    out = await nodes.plan_query({"conversation_id": 1, "question": "그거 기한은?"})

    assert out == {"rewritten_question": "결제팀 출장비 정산 기한", "complexity": "simple", "search_queries": ["결제팀 출장비 정산 기한"]}
    assert len(gen.calls) == 1
    nodes._db.append_user_message.assert_awaited_once_with(1, "그거 기한은?", "결제팀 출장비 정산 기한")


@pytest.mark.asyncio
async def test_plan_query_node_without_planner_only_rewrites():
    memory = MagicMock()
    memory.rewrite_question = AsyncMock(return_value="결제팀 출장비 정산 기한")
    nodes = make_nodes(MockGenerator("쓰이면 안 된다"), None, memory)

    out = await nodes.plan_query({"conversation_id": 1, "question": "그거 기한은?"})

    assert out["search_queries"] == ["결제팀 출장비 정산 기한"] and out["complexity"] == "simple"


@pytest.mark.asyncio
async def test_hyde():
    mock_doc = "사내 결제 승인은 5단계로 진행되며, 팀장 승인 후 재무팀의 최종 확인이 필요합니다."
    gen = MockGenerator(mock_doc)
    doc = await HydeService(gen).generate_hypothetical_document("결제 승인 절차")

    assert doc == mock_doc
    assert gen.calls == [(HYDE_SYSTEM, "결제 승인 절차")]


@pytest.mark.asyncio
async def test_hyde_returns_none_on_echo():
    """입력이 그대로 돌아오면 가상 문서가 아니다. 질문을 두 번 검색하지 않게 None 을 준다."""
    assert await HydeService(EchoGenerator()).generate_hypothetical_document("결제 승인 절차") is None


@pytest.mark.asyncio
async def test_real_generators_route_through_complete(monkeypatch):
    """실제 생성기로 돌려 본다. generate 는 근거가 비면 모델을 부르지 않고 끝나므로,
    질의 확장이 generate 를 쓰면 HyDE·계획이 조용히 아무 일도 하지 않는다."""
    groq = GroqAnswerGenerator(Settings(groq_api_key="dummy"))
    calls: list[str] = []

    async def fake_complete(system_prompt, user_prompt, max_tokens):
        calls.append(system_prompt)
        if system_prompt == HYDE_SYSTEM:
            return "결제는 팀장 승인 후 재무팀이 확인한다."
        return plan_reply(kind="complex", subs=["인증팀 정책", "결제팀 정책"])

    monkeypatch.setattr(groq, "complete", fake_complete)
    assert await HydeService(groq).generate_hypothetical_document("결제 승인 절차") == "결제는 팀장 승인 후 재무팀이 확인한다."
    assert (await QueryPlanner(groq).plan("인증팀과 결제팀 비교")).sub_queries == ("인증팀 정책", "결제팀 정책")
    assert calls == [HYDE_SYSTEM, QUERY_PLAN_SYSTEM]

    # 키가 없으면(근거 발췌) 확장 없이 원래 질문 하나로 검색한다
    extractive = ExtractiveAnswerGenerator()
    assert await HydeService(extractive).generate_hypothetical_document("결제 승인 절차") is None
    assert await QueryPlanner(extractive).plan("인증팀과 결제팀 비교") == QueryPlan("인증팀과 결제팀 비교")

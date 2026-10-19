"""근거 충분성 판정 — 판정기 응답 해석과 판정 노드의 경로."""
from unittest.mock import MagicMock

import pytest
from history_shared.metrics import EVIDENCE_VERDICTS

from app.graph.nodes import GraphNodes
from app.search.evidence_judge import EvidenceJudge
from app.search.models import SearchHit


def hit(content: str, rerank: float | None, similarity: float | None = 0.7) -> SearchHit:
    return SearchHit(1, 1, 0, content, "문서 > 절", "", rerank or 0.0, similarity, rerank)


class FakeGenerator:
    def __init__(self, reply: str | None = None, error: Exception | None = None):
        self.reply, self.error, self.calls = reply, error, 0

    async def complete(self, system_prompt, user_prompt, max_tokens):
        self.calls += 1
        if self.error:
            raise self.error
        # 실제 생성기처럼, 답이 없으면 입력을 그대로 돌려준다
        return user_prompt if self.reply is None else self.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, answerable, evaluated",
    [
        ('{"answerable": true}', True, True),
        ('판정: {"answerable": false} 입니다', False, True),
        # 문자열 "false" 는 판정으로 인정하지 않는다(참으로 받으면 거꾸로 통과시킨다)
        ('{"answerable": "false"}', True, False),
        ("모르겠다", True, False),
        (None, True, False),  # LLM 없음 — 입력을 그대로 돌려받았다
    ],
)
async def test_judge_parses_only_boolean_verdicts(reply, answerable, evaluated):
    result = await EvidenceJudge(FakeGenerator(reply)).judge("질문", [hit("근거", 0.1)])
    assert (result.answerable, result.evaluated) == (answerable, evaluated)


@pytest.mark.asyncio
async def test_judge_failure_passes():
    result = await EvidenceJudge(FakeGenerator(error=RuntimeError("down"))).judge("질문", [hit("근거", 0.1)])
    assert (result.answerable, result.evaluated) == (True, False)


def make_nodes(generator: FakeGenerator, judge: bool = True, min_vector_similarity: float = 0.62) -> GraphNodes:
    return GraphNodes(
        db=MagicMock(), auth=MagicMock(), hybrid_search=MagicMock(), source_resolver=MagicMock(),
        answer_cache=MagicMock(), memory=MagicMock(), generator=generator,
        evidence_judge=EvidenceJudge(generator) if judge else None, min_vector_similarity=min_vector_similarity,
    )


def verdicts(outcome: str) -> float:
    return EVIDENCE_VERDICTS.labels(outcome=outcome)._value.get()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hits, reply, route, outcome, llm_calls",
    [
        # 벡터 유사도가 낮으면 LLM 을 부르지 않고 거절한다
        ([hit("보안 로그 보관 기간은 1년", 0.9, similarity=0.55)], '{"answerable": true}', "no_evidence_no_hits", "low_similarity", 0),
        # 리랭커 점수가 높아도 LLM 판정을 따른다 — 주제가 맞아도 답이 없을 수 있다
        ([hit("보안 로그 보관 기간은 1년", 0.96)], '{"answerable": false}', "no_evidence_no_hits", "judged_unanswerable", 1),
        # 낱말이 전혀 안 겹쳐도(사용자 말투) LLM 이 답이 있다고 보면 통과한다
        ([hit("보안 로그 보관 기간은 1년", 0.001)], '{"answerable": true}', "resolve_sources", "judged_answerable", 1),
        # 판정 실패는 1차 거름으로 정한다 — 낱말이 겹치면 통과, 안 겹치고 점수도 낮으면 거절
        ([hit("보안 로그 보관 기간은 1년", 0.05)], "???", "resolve_sources", "unjudged_gate_passed", 1),
        ([hit("전혀 다른 내용의 문단", 0.0001, similarity=0.63)], "???", "no_evidence_no_hits", "unjudged_gate_rejected", 1),
        # 벡터 유사도가 없는(전문검색으로만 걸린) 근거는 거르지 않고 판정한다
        ([hit("보안 로그 보관 기간은 1년", 0.05, similarity=None)], '{"answerable": true}', "resolve_sources", "judged_answerable", 1),
    ],
)
async def test_judge_evidence_routes(hits, reply, route, outcome, llm_calls):
    gen = FakeGenerator(reply)
    nodes = make_nodes(gen)
    before = verdicts(outcome)

    state = {"rewritten_question": "보안 로그 보관 기간은?", "hits": hits}
    state.update(await nodes.judge_evidence(state))

    assert nodes.route_after_judge(state) == route
    assert gen.calls == llm_calls
    assert verdicts(outcome) == before + 1


@pytest.mark.asyncio
async def test_judge_disabled_keeps_gate_only_behaviour():
    gen = FakeGenerator('{"answerable": false}')
    nodes = make_nodes(gen, judge=False)
    state = {"rewritten_question": "보안 로그 보관 기간은?", "hits": [hit("보안 로그 보관 기간은 1년", 0.05)]}
    state.update(await nodes.judge_evidence(state))
    assert nodes.route_after_judge(state) == "resolve_sources"
    assert state["evidence_outcome"] == "gate_passed"
    assert gen.calls == 0


def test_gate_vector_branch_reads_vector_similarity_not_reranked_score():
    """재순위화 후 score 는 리랭커 점수다. 벡터 갈래가 score 를 보면 리랭커 점수를 벡터 기준과 비교하게 된다."""
    from app.search.relevance_gate import EvidenceGate, has_evidence

    gate = EvidenceGate(rerank_score=0.99, vector_score=0.65, min_coverage=1.0)
    reranked_high_but_far = SearchHit(1, 1, 0, "무관한 문단", "", "", 0.9, 0.3, 0.9)
    close_but_low_rerank = SearchHit(1, 1, 0, "무관한 문단", "", "", 0.01, 0.7, 0.01)
    assert has_evidence("질문 낱말", [reranked_high_but_far], gate) is False
    assert has_evidence("질문 낱말", [close_but_low_rerank], gate) is True


class FakeRedis:
    def __init__(self, broken: bool = False):
        self.data, self.broken = {}, broken

    async def get(self, key):
        if self.broken:
            raise ConnectionError("down")
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("down")
        self.data[key] = value


@pytest.mark.asyncio
async def test_judge_cache_skips_llm_for_same_question_and_evidence():
    from app.search.evidence_judge import JudgeCache

    gen = FakeGenerator('{"answerable": false}')
    judge = EvidenceJudge(gen, cache=JudgeCache(FakeRedis()))
    evidence = [hit("보안 로그 보관 기간은 1년", 0.05)]

    first = await judge.judge("보안 로그  보관 기간은?", evidence)
    # 공백·대소문자만 다른 같은 질문은 캐시를 쓴다
    second = await judge.judge("보안 로그 보관 기간은?", evidence)

    assert (first.answerable, second.answerable) == (False, False)
    assert gen.calls == 1


@pytest.mark.asyncio
async def test_judge_cache_key_changes_with_document_version():
    from app.search.evidence_judge import JudgeCache

    gen = FakeGenerator('{"answerable": true}')
    judge = EvidenceJudge(gen, cache=JudgeCache(FakeRedis()))
    v1 = SearchHit(1, 1, 0, "내용", "", "", 0.1, 0.7, 0.1)
    v2 = SearchHit(1, 2, 0, "고친 내용", "", "", 0.1, 0.7, 0.1)

    await judge.judge("질문", [v1])
    await judge.judge("질문", [v2])

    assert gen.calls == 2


@pytest.mark.asyncio
async def test_failed_judgement_is_not_cached_and_broken_cache_is_ignored():
    from app.search.evidence_judge import JudgeCache

    gen = FakeGenerator("???")
    cache = FakeRedis()
    judge = EvidenceJudge(gen, cache=JudgeCache(cache))
    assert (await judge.judge("질문", [hit("근거", 0.1)])).evaluated is False
    assert cache.data == {}

    broken = EvidenceJudge(FakeGenerator('{"answerable": true}'), cache=JudgeCache(FakeRedis(broken=True)))
    assert (await broken.judge("질문", [hit("근거", 0.1)])).answerable is True


@pytest.mark.asyncio
async def test_judge_shows_only_top_evidence_compactly():
    seen = {}

    class Capture(FakeGenerator):
        async def complete(self, system_prompt, user_prompt, max_tokens):
            seen["prompt"] = user_prompt
            return '{"answerable": true}'

    content = "[문서] 보안 로그 결정 | 작성일 2026-05-18 | 절 보안 로그 결정 > 결정 사항\n\n" + "가" * 1000
    hits = [SearchHit(1, 1, i, content, "보안 로그 결정 > 결정 사항", "", 0.1, 0.7, 0.1) for i in range(5)]
    await EvidenceJudge(Capture(), max_evidence=3, max_chars=400).judge("질문", hits)

    prompt = seen["prompt"]
    assert "[3]" in prompt and "[4]" not in prompt
    assert "작성일" not in prompt and "(보안 로그 결정 > 결정 사항)" in prompt
    assert prompt.count("가") == 3 * 400


def test_frozen_judge_cases_match_questions_and_passages():
    import json

    from evaluation.dataset import DATASETS, load_passages, load_questions

    cases = json.loads((DATASETS / "judge_cases_ko.json").read_text(encoding="utf-8"))["cases"]
    keys = {p["key"] for p in load_passages()}
    ids = {q.id for q in load_questions()}
    assert {c["id"] for c in cases} == ids
    assert all(h["key"] in keys for c in cases for h in c["hits"])


@pytest.mark.asyncio
async def test_judge_cache_is_not_shared_across_prompts():
    from app.search.evidence_judge import JudgeCache

    redis = FakeRedis()
    evidence = [hit("근거", 0.1)]
    old = FakeGenerator('{"answerable": true}')
    new = FakeGenerator('{"answerable": false}')
    await EvidenceJudge(old, cache=JudgeCache(redis)).judge("질문", evidence)
    result = await EvidenceJudge(new, cache=JudgeCache(redis), system_prompt="바뀐 기준").judge("질문", evidence)

    assert result.answerable is False and new.calls == 1


def test_similarity_floor_is_off_for_fallback_embedding():
    """기준값은 실제 임베딩 모델로 잰 값이다. 대체 임베딩(해시)에는 적용하지 않는다."""
    from history_shared.embeddings import LocalHashingEmbedder

    from app.dependencies import similarity_floor

    gemini = MagicMock(model_name="gemini-embedding-001")
    assert similarity_floor(0.62, gemini) == 0.62
    assert similarity_floor(0.62, LocalHashingEmbedder(768)) == 0.0


@pytest.mark.asyncio
async def test_fallback_embedding_question_reaches_the_gate():
    """키 없이 돌 때: 대체 임베딩은 정답 문단도 유사도가 0.2~0.3 이라, 기준을 끄면 LLM 없이 1차 거름으로 정한다."""
    gen = FakeGenerator(None)  # LLM 없음 — 입력을 그대로 돌려받는다
    nodes = make_nodes(gen, min_vector_similarity=0.0)
    state = {"rewritten_question": "보안 로그 보관 기간은?", "hits": [hit("보안 로그 보관 기간은 1년", 0.05, similarity=0.28)]}
    state.update(await nodes.judge_evidence(state))
    assert nodes.route_after_judge(state) == "resolve_sources"
    assert state["evidence_outcome"] == "unjudged_gate_passed"

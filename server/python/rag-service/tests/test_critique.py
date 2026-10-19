import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.generation.critique import CRITIQUE_SYSTEM, CritiqueGenerator
from app.generation.models import AnswerStatus
from app.graph.nodes import GraphNodes
from app.search.models import SearchHit
from app.search.reranker import CrossEncoderReranker


class MockGenerator:
    """경량 호출(complete)만 흉내 낸다. generate 를 부르면 실패한다."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls: list[tuple[str, str]] = []

    async def generate(self, request, on_chunk) -> None:
        raise AssertionError("critique 가 답변 생성(generate)을 불렀다")

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.response_text


class EchoGenerator(MockGenerator):
    """LLM 이 없거나 실패했을 때의 complete — 입력을 그대로 돌려준다."""

    def __init__(self):
        super().__init__("")

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        return user_prompt


def _hit(content: str) -> SearchHit:
    return SearchHit(
        document_id=1,
        document_version=1,
        chunk_no=0,
        content=content,
        section_path="",
        chunk_tag="",
        score=0.9,
        vector_similarity=0.85,
    )


@pytest.mark.asyncio
async def test_critique_evaluation_success():
    mock_json = """
    ```json
    {
        "answered": true,
        "confidence": 0.95,
        "missing": [],
        "nextQuery": null
    }
    ```
    """
    gen = MockGenerator(mock_json)
    result = await CritiqueGenerator(gen).evaluate(
        "결제 정책은?", "매월 25일 정산입니다.", [_hit("결제 정책은 매월 25일에 정산됩니다.")]
    )
    assert result.evaluated is True
    assert result.answered is True
    assert result.confidence == 0.95
    assert result.missing == []
    assert result.next_query is None
    # 지시문은 시스템 프롬프트로 간다
    assert gen.calls[0][0] == CRITIQUE_SYSTEM
    assert "결제 정책은?" in gen.calls[0][1]


@pytest.mark.asyncio
async def test_critique_evaluation_hallucination_low_confidence():
    mock_json = """
    {
        "answered": false,
        "confidence": 0.4,
        "missing": ["결제 승인자 정보"],
        "nextQuery": "결제 승인 권한자 규정"
    }
    """
    result = await CritiqueGenerator(MockGenerator(mock_json)).evaluate(
        "승인자는 누구인가요?", "CEO가 승인합니다.", [_hit("단순 안내문입니다.")]
    )
    assert result.evaluated is True
    assert result.answered is False
    assert result.confidence == 0.4
    assert "결제 승인자 정보" in result.missing
    assert result.next_query == "결제 승인 권한자 규정"


@pytest.mark.asyncio
async def test_critique_sees_every_cited_evidence():
    """답변이 [5] 를 인용했는데 평가자가 [1]~[3] 만 보면 뒷받침된 답변도 "근거 없음"으로 깎인다."""
    gen = MockGenerator('{"answered": true, "confidence": 0.9}')
    evidence = [_hit(f"근거 {i} " + "가" * 600) for i in range(1, 6)]
    await CritiqueGenerator(gen).evaluate("질문", "답변[5]", evidence)

    user_prompt = gen.calls[0][1]
    for i in range(1, 6):
        assert f"[{i}] 근거 {i}" in user_prompt
    # 청크를 250자로 자르지 않는다
    assert "가" * 600 in user_prompt


@pytest.mark.asyncio
async def test_critique_fallback_on_parse_error():
    result = await CritiqueGenerator(MockGenerator("이상한 출력")).evaluate("질문", "답변", [_hit("본문")])
    assert result.evaluated is False
    assert result.confidence == 1.0
    assert result.answered is True


@pytest.mark.asyncio
async def test_critique_fallback_on_echo():
    """LLM 이 없으면 complete 가 입력을 돌려준다. 그걸 평가 결과로 파싱하지 않는다."""
    result = await CritiqueGenerator(EchoGenerator()).evaluate("질문", "답변", [_hit("본문")])
    assert result.evaluated is False
    assert result.confidence == 1.0


# ---- 그래프 노드: 신뢰도가 낮으면 상태를 낮추고, 낮춘 답변은 캐시하지 않는다 ----


def _nodes(generator, answer_cache=None, db=None) -> GraphNodes:
    return GraphNodes(
        db=db or MagicMock(),
        auth=MagicMock(),
        hybrid_search=MagicMock(),
        source_resolver=MagicMock(),
        answer_cache=answer_cache or MagicMock(),
        memory=MagicMock(),
        generator=generator,
        reranker=CrossEncoderReranker(enabled=False),
        candidate_count=20,
        reranker_min_score=0.0,
    )


def _state(status: AnswerStatus) -> dict:
    return {
        "question": "승인자는 누구인가요?",
        "rewritten_question": "승인자는 누구인가요?",
        "answer_text": "CEO가 승인합니다.",
        "evidence": [_hit("단순 안내문입니다.")],
        "status": status,
    }


@pytest.mark.asyncio
async def test_node_downgrades_low_confidence_answer():
    nodes = _nodes(MockGenerator('{"answered": false, "confidence": 0.4, "missing": ["승인자"]}'))
    update = await nodes.critique(_state(AnswerStatus.OK))
    assert update["status"] == AnswerStatus.LOW_CONFIDENCE
    assert update["confidence"] == 0.4


@pytest.mark.asyncio
async def test_node_keeps_status_when_confident():
    nodes = _nodes(MockGenerator('{"answered": true, "confidence": 0.9, "missing": []}'))
    update = await nodes.critique(_state(AnswerStatus.OK))
    assert "status" not in update
    assert update["confidence"] == 0.9


@pytest.mark.asyncio
async def test_node_keeps_status_when_not_evaluated():
    """평가를 못 했으면(키 없음·실패) 답변을 깎지 않는다."""
    assert await _nodes(EchoGenerator()).critique(_state(AnswerStatus.OK)) == {}


@pytest.mark.asyncio
async def test_node_skips_non_ok_answers():
    """근거 발췌·한도 초과·장애 답변은 상태가 이미 그 사실을 말한다. 모델을 부르지 않는다."""
    gen = MockGenerator('{"confidence": 0.1}')
    nodes = _nodes(gen)
    for status in (AnswerStatus.LOW_CONFIDENCE, AnswerStatus.DEGRADED, AnswerStatus.RATE_LIMITED):
        assert await nodes.critique(_state(status)) == {}
    assert gen.calls == []


@pytest.mark.asyncio
async def test_persist_caches_only_ok_answers():
    """캐시에서 꺼낸 답변은 OK 로 나간다. LOW_CONFIDENCE 를 캐시하면 다음 질문에서 표시가 사라진다."""
    db = MagicMock()
    db.append_assistant_message = AsyncMock(return_value=1)
    cache = MagicMock()
    cache.put = AsyncMock()
    nodes = _nodes(EchoGenerator(), answer_cache=cache, db=db)
    scope = MagicMock()
    scope.to_scope_keys.return_value = ["org:1"]

    for status in (AnswerStatus.LOW_CONFIDENCE, AnswerStatus.OK):
        state = _state(status) | {
            "conversation_id": 1, "model_name": "m", "scope": scope, "started_at": time.monotonic(),
        }
        await nodes.persist_answer(state)

    assert cache.put.await_count == 1

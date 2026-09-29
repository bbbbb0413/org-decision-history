import pytest
from app.generation.base import AnswerGenerator
from app.generation.critique import CritiqueGenerator
from app.generation.models import AnswerChunk, AnswerRequest
from app.search.models import SearchHit


class MockGenerator(AnswerGenerator):
    def __init__(self, response_text: str):
        self.response_text = response_text

    async def generate(self, request: AnswerRequest, on_chunk) -> None:
        await on_chunk(AnswerChunk.delta(self.response_text))
        await on_chunk(AnswerChunk.finished(None, "mock"))


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
    critique = CritiqueGenerator(MockGenerator(mock_json))
    evidence = [
        SearchHit(
            document_id=1,
            document_version=1,
            chunk_no=0,
            content="결제 정책은 매월 25일에 정산됩니다.",
            section_path="",
            chunk_tag="",
            score=0.9,
            vector_similarity=0.85,
        )
    ]

    result = await critique.evaluate("결제 정책은?", "매월 25일 정산입니다.", evidence)
    assert result.answered is True
    assert result.confidence == 0.95
    assert result.missing == []
    assert result.next_query is None


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
    critique = CritiqueGenerator(MockGenerator(mock_json))
    evidence = [
        SearchHit(
            document_id=1,
            document_version=1,
            chunk_no=0,
            content="단순 안내문입니다.",
            section_path="",
            chunk_tag="",
            score=0.5,
            vector_similarity=0.6,
        )
    ]

    result = await critique.evaluate("승인자는 누구인가요?", "CEO가 승인합니다.", evidence)
    assert result.answered is False
    assert result.confidence == 0.4
    assert "결제 승인자 정보" in result.missing
    assert result.next_query == "결제 승인 권한자 규정"


@pytest.mark.asyncio
async def test_critique_fallback_on_parse_error():
    critique = CritiqueGenerator(MockGenerator("이상한 출력"))
    evidence = [
        SearchHit(
            document_id=1,
            document_version=1,
            chunk_no=0,
            content="본문",
            section_path="",
            chunk_tag="",
            score=0.5,
            vector_similarity=0.5,
        )
    ]

    result = await critique.evaluate("질문", "답변", evidence)
    assert result.confidence == 1.0
    assert result.answered is True

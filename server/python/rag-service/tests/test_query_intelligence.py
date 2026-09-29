import pytest
from app.search.complexity_router import QueryComplexityRouter
from app.search.decomposer import QueryDecomposer
from app.search.hyde import HydeService
from app.generation.base import AnswerGenerator
from app.generation.models import AnswerChunk, AnswerRequest


class MockGenerator(AnswerGenerator):
    def __init__(self, response_text: str):
        self.response_text = response_text

    async def generate(self, request: AnswerRequest, on_chunk) -> None:
        await on_chunk(AnswerChunk.delta(self.response_text))
        await on_chunk(AnswerChunk.finished(None, "mock"))


def test_complexity_router():
    router = QueryComplexityRouter()

    # 복합/비교 질문 -> complex
    assert router.route("인증팀과 결제팀의 정책 차이점을 비교해줘") == "complex"
    assert router.route("Why was MongoDB chosen versus PostgreSQL?") == "complex"
    assert router.route("A정책이 적용된 원인은 무엇인가요?") == "complex"
    assert router.route("이 결정을 내린 이유가 무엇인가요? 그리고 대안은 무엇이었나요?") == "complex"

    # 단문/키워드 빈약 질문 (3~10 단어) -> hyde
    assert router.route("결제 승인 정책 알려줘") == "hyde"
    assert router.route("회원가입 메일 인증 방식") == "hyde"

    # 2단어 이하 극단문 -> simple
    assert router.route("로그인") == "simple"


@pytest.mark.asyncio
async def test_decomposer():
    mock_response = "- 인증팀 정책은 무엇인가?\n- 결제팀 정책은 무엇인가?"
    decomposer = QueryDecomposer(MockGenerator(mock_response))

    queries = await decomposer.decompose("인증팀과 결제팀 정책 차이점")
    assert len(queries) == 2
    assert "인증팀 정책은 무엇인가?" in queries
    assert "결제팀 정책은 무엇인가?" in queries


@pytest.mark.asyncio
async def test_hyde():
    mock_doc = "사내 결제 승인은 5단계로 진행되며, 팀장 승인 후 재무팀의 최종 확인이 필요합니다."
    hyde = HydeService(MockGenerator(mock_doc))

    doc = await hyde.generate_hypothetical_document("결제 승인 절차")
    assert doc is not None
    assert "5단계" in doc

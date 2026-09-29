import pytest
from app.search.models import SearchHit
from app.search.reranker import CrossEncoderReranker


def make_hit(doc_id: int, chunk_no: int, content: str, score: float = 0.5) -> SearchHit:
    return SearchHit(
        document_id=doc_id,
        document_version=1,
        chunk_no=chunk_no,
        content=content,
        section_path="",
        chunk_tag="",
        score=score,
        vector_similarity=score,
    )


@pytest.mark.asyncio
async def test_reranker_disabled_fallback():
    reranker = CrossEncoderReranker(enabled=False)
    hits = [
        make_hit(1, 0, "문서 A 내용", 0.9),
        make_hit(2, 0, "문서 B 내용", 0.8),
        make_hit(3, 0, "문서 C 내용", 0.7),
    ]
    results = await reranker.rerank("아무 질문", hits, top_k=2)
    assert len(results) == 2
    assert results[0].document_id == 1
    assert results[1].document_id == 2


@pytest.mark.asyncio
async def test_reranker_empty_inputs():
    reranker = CrossEncoderReranker(enabled=False)
    assert await reranker.rerank("", [make_hit(1, 0, "내용")]) == []
    assert await reranker.rerank("질문", []) == []


@pytest.mark.asyncio
async def test_reranker_mock_scoring():
    class MockRanker:
        def rerank(self, req):
            # req.passages: [{"id": 0, "text": ...}, ...]
            # 질문과 "재택" 또는 "원격" 단어가 포함된 문장에 높은 점수 부여
            results = []
            for p in req.passages:
                text = p["text"]
                score = 0.95 if ("재택" in text or "원격" in text) else 0.15
                results.append({"id": p["id"], "score": score, "text": text})
            results.sort(key=lambda x: x["score"], reverse=True)
            return results

    reranker = CrossEncoderReranker(enabled=False)
    reranker._ranker = MockRanker()
    reranker.enabled = True

    hits = [
        make_hit(1, 0, "점심 식대는 12,000원까지 법인카드로 지원됩니다."),  # irrelevant
        make_hit(2, 0, "임직원은 주 2회 재택근무 및 원격 근무를 신청할 수 있습니다."),  # highly relevant
        make_hit(3, 0, "보안 규정상 원격 접속 시 OTP 인증이 필수입니다."),  # relevant
    ]

    results = await reranker.rerank("원격 근무 규정이 어떻게 되나요?", hits, top_k=2, min_score=0.3)

    assert len(results) == 2
    assert results[0].document_id in (2, 3)
    assert results[1].document_id in (2, 3)
    # irrelevant doc (id=1) must have been filtered or ranked last
    assert all(r.document_id != 1 for r in results)
    assert results[0].rerank_score is not None
    assert results[0].rerank_score >= 0.9


@pytest.mark.asyncio
async def test_reranker_min_score_filter():
    class LowScoreRanker:
        def rerank(self, req):
            return [{"id": p["id"], "score": 0.2, "text": p["text"]} for p in req.passages]

    reranker = CrossEncoderReranker(enabled=False)
    reranker._ranker = LowScoreRanker()
    reranker.enabled = True

    hits = [make_hit(1, 0, "낮은 관련도 내용")]
    results = await reranker.rerank("질문", hits, min_score=0.35)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_reranker_error_graceful_fallback():
    class BrokenRanker:
        def rerank(self, req):
            raise RuntimeError("Inference session crashed")

    reranker = CrossEncoderReranker(enabled=False)
    reranker._ranker = BrokenRanker()
    reranker.enabled = True

    hits = [
        make_hit(1, 0, "문서 1", 0.9),
        make_hit(2, 0, "문서 2", 0.8),
    ]
    # 에러가 발생해도 예외를 던지지 않고 1차 검색 순서대로 안전하게 반환되어야 함
    results = await reranker.rerank("질문", hits, top_k=1)
    assert len(results) == 1
    assert results[0].document_id == 1

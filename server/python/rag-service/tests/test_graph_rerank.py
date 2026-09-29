import pytest
from unittest.mock import MagicMock

from app.graph.build import build_graph
from app.graph.nodes import GraphNodes
from app.search.models import SearchHit
from app.search.reranker import CrossEncoderReranker


def make_hit(doc_id: int, content: str, score: float = 0.8) -> SearchHit:
    return SearchHit(
        document_id=doc_id,
        document_version=1,
        chunk_no=0,
        content=content,
        section_path="",
        chunk_tag="",
        score=score,
        vector_similarity=score,
    )


@pytest.mark.asyncio
async def test_graph_compilation_with_reranker():
    mock_db = MagicMock()
    mock_auth = MagicMock()
    mock_search = MagicMock()
    mock_resolver = MagicMock()
    mock_cache = MagicMock()
    mock_memory = MagicMock()
    mock_generator = MagicMock()

    reranker = CrossEncoderReranker(enabled=False)

    nodes = GraphNodes(
        db=mock_db,
        auth=mock_auth,
        hybrid_search=mock_search,
        source_resolver=mock_resolver,
        answer_cache=mock_cache,
        memory=mock_memory,
        generator=mock_generator,
        reranker=reranker,
        candidate_count=20,
        reranker_top_k=5,
        reranker_min_score=0.0,
    )

    # 그래프 컴파일 검증
    graph = build_graph(nodes)
    assert graph is not None

    # 노드 존재 확인
    node_names = set(graph.nodes.keys())
    assert "rerank" in node_names
    assert "retrieve" in node_names
    assert "guardrail" in node_names
    assert "critique" in node_names


@pytest.mark.asyncio
async def test_nodes_rerank_integration():
    mock_db = MagicMock()
    mock_auth = MagicMock()
    mock_search = MagicMock()
    mock_resolver = MagicMock()
    mock_cache = MagicMock()
    mock_memory = MagicMock()
    mock_generator = MagicMock()

    class CustomRanker:
        def rerank(self, req):
            # 역순으로 정렬
            return [{"id": p["id"], "score": float(10 - p["id"]), "text": p["text"]} for p in req.passages]

    reranker = CrossEncoderReranker(enabled=False)
    reranker._ranker = CustomRanker()
    reranker.enabled = True

    nodes = GraphNodes(
        db=mock_db,
        auth=mock_auth,
        hybrid_search=mock_search,
        source_resolver=mock_resolver,
        answer_cache=mock_cache,
        memory=mock_memory,
        generator=mock_generator,
        reranker=reranker,
        candidate_count=20,
        reranker_top_k=2,
        reranker_min_score=0.0,
    )

    state = {
        "rewritten_question": "인증 관련 변경사항",
        "hits": [
            make_hit(1, "문서 1"),
            make_hit(2, "문서 2"),
            make_hit(3, "문서 3"),
        ],
    }

    result = await nodes.rerank(state)
    hits = result["hits"]
    assert len(hits) == 2
    # CustomRanker: id 0 gets score 10, id 1 gets score 9
    assert hits[0].document_id == 1
    assert hits[1].document_id == 2
    assert hits[0].rerank_score == 10.0
    assert hits[1].rerank_score == 9.0

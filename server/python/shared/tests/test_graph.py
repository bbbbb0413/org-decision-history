"""history_shared.graph — rag-service(graph_rag/)·governance-worker(graph_sync/)가
각자 갖고 있던 거의 동일한 코드를 통합한 모듈이다. 두 서비스가 쓰던
테스트를 합쳐서 여기 한 곳에서만 검증한다.

라벨·관계 타입·키 필드는 Cypher 파라미터 바인딩이 안 돼 f-string으로 쿼리에 직접
들어간다(client.py). 허용 목록 밖의 값(LLM이 프롬프트 인젝션에 낚여 낸 값 포함)이
그 문자열 조립까지 도달하지 않는지를 검증한다 — 도달하면 임의 Cypher가 실행된다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from history_shared.graph.models import SubgraphResult
from history_shared.graph.extractor import DecisionGraphExtractor
from history_shared.graph.client import Neo4jGraphClient
from history_shared.graph.sync import GraphSyncService


class _StubGenerator:
    """CompletionGenerator Protocol — .complete()만 있으면 된다."""

    def __init__(self, response_text: str):
        self.response_text = response_text

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
        return self.response_text


# ─── 1. Models ──────────────────────────────────────────────

def test_subgraph_result_to_context_str():
    subgraph = SubgraphResult(
        triples=[
            "[2026 하반기 근무제도 개정안] ──[DEPRECATES]──> [Policy: 전사 재택근무 정책]",
            "[인증팀 세션 정책 개정] ──[AFFECTS]──> [System: 결제 게이트웨이]",
        ]
    )
    context_str = subgraph.to_context_str()
    assert "[사내 의사결정 인과 관계망 (Knowledge Subgraph)]:" in context_str
    assert "- [2026 하반기 근무제도 개정안]" in context_str
    assert "- [인증팀 세션 정책 개정]" in context_str


def test_subgraph_result_empty():
    subgraph = SubgraphResult()
    assert subgraph.to_context_str() == ""


# ─── 2. Extractor ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_decision_graph_extractor():
    mock_json = """
    ```json
    {
      "systems": ["인증 게이트웨이", "정기결제 배치 시스템"],
      "teams": ["인증팀", "결제개발팀"],
      "relations": [
        {
          "source_type": "Decision",
          "rel_type": "AFFECTS",
          "target_type": "System",
          "target_name": "정기결제 배치 시스템",
          "description": "토큰 유효기간 축소로 인한 타임아웃 영향"
        },
        {
          "source_type": "Decision",
          "rel_type": "DEPRECATES",
          "target_type": "Decision",
          "target_name": "과거 세션 무제한 정책",
          "description": "보안 강화로 인한 폐기"
        }
      ]
    }
    ```
    """
    extractor = DecisionGraphExtractor(_StubGenerator(mock_json))
    elements = await extractor.extract_graph_elements(
        doc_id=10,
        title="세션 보안 강화 방안",
        content="인증팀은 세션 만료 시간을 30분으로 축소한다...",
        author_team="인증팀",
    )

    assert "인증 게이트웨이" in elements["systems"]
    assert "정기결제 배치 시스템" in elements["systems"]
    assert "인증팀" in elements["teams"]
    assert "결제개발팀" in elements["teams"]

    rels = elements["relations"]
    rel_types = [r["rel_type"] for r in rels]
    assert "AFFECTS" in rel_types
    assert "DEPRECATES" in rel_types
    assert "DECIDED_BY" in rel_types


@pytest.mark.asyncio
async def test_decision_graph_extractor_fallback():
    extractor = DecisionGraphExtractor(_StubGenerator("Invalid JSON"))
    elements = await extractor.extract_graph_elements(
        doc_id=10,
        title="일반 공지",
        content="일반 공지 사항입니다.",
        author_team="플랫폼본부",
    )
    assert elements["systems"] == []
    assert elements["teams"] == ["플랫폼본부"]
    assert len(elements["relations"]) == 1
    assert elements["relations"][0]["rel_type"] == "DECIDED_BY"


@pytest.mark.asyncio
async def test_extractor_drops_relation_with_disallowed_rel_type():
    """LLM이 허용 목록 밖의 rel_type을 내면 그 관계를 통째로 버린다."""
    llm_json = """
    {
      "systems": [],
      "teams": [],
      "relations": [
        {"rel_type": "DROP_TABLE", "target_type": "System", "target_name": "결제 시스템"}
      ]
    }
    """
    extractor = DecisionGraphExtractor(_StubGenerator(llm_json))

    result = await extractor.extract_graph_elements(doc_id=1, title="테스트 문서", content="본문")

    assert result["relations"] == []


@pytest.mark.asyncio
async def test_extractor_keeps_relation_with_allowlisted_rel_type():
    llm_json = """
    {
      "systems": [],
      "teams": [],
      "relations": [
        {"rel_type": "affects", "target_type": "System", "target_name": "결제 시스템"}
      ]
    }
    """
    extractor = DecisionGraphExtractor(_StubGenerator(llm_json))

    result = await extractor.extract_graph_elements(doc_id=1, title="테스트 문서", content="본문")

    assert len(result["relations"]) == 1
    assert result["relations"][0]["rel_type"] == "AFFECTS"


# ─── 3. Neo4jGraphClient — 목 드라이버로 기본 동작 ──────────

@pytest.mark.asyncio
async def test_neo4j_client_operations():
    client = Neo4jGraphClient("bolt://localhost:7687", "neo4j", "password")

    mock_driver = MagicMock()
    mock_session = AsyncMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session
    client._driver = mock_driver

    await client.upsert_decision(doc_id=1, title="테스트 문서", status="CURRENT")
    mock_session.run.assert_called()

    await client.upsert_node("System", "name", "인증게이트웨이")
    mock_session.run.assert_called()

    await client.upsert_edge("Decision", "id", 1, "AFFECTS", "System", "name", "인증게이트웨이")
    mock_session.run.assert_called()


# ─── 4. Neo4jGraphClient — Cypher Injection 방어 (FakeDriver) ─

class FakeSession:
    def __init__(self):
        self.queries: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, query: str, **kwargs):
        self.queries.append(query)
        return _EmptyResult()


class _EmptyResult:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeDriver:
    def __init__(self):
        self.session_obj = FakeSession()

    def session(self):
        return self.session_obj


def _client_with_fake_driver() -> tuple[Neo4jGraphClient, FakeDriver]:
    client = Neo4jGraphClient("bolt://unused", "u", "p")
    driver = FakeDriver()
    client._driver = driver  # connect() 를 거치지 않고 연결된 상태만 흉내낸다
    return client, driver


@pytest.mark.asyncio
async def test_upsert_node_rejects_label_outside_allowlist():
    client, driver = _client_with_fake_driver()

    await client.upsert_node("Decision}) DETACH DELETE n //", "name", "x")

    assert driver.session_obj.queries == []


@pytest.mark.asyncio
async def test_upsert_node_accepts_allowlisted_label():
    client, driver = _client_with_fake_driver()

    await client.upsert_node("System", "name", "결제 게이트웨이")

    assert len(driver.session_obj.queries) == 1
    assert "MERGE (n:System" in driver.session_obj.queries[0]


@pytest.mark.asyncio
async def test_upsert_edge_rejects_rel_type_outside_allowlist():
    client, driver = _client_with_fake_driver()

    await client.upsert_edge(
        "Decision", "id", 1,
        "AFFECTS}) DETACH DELETE a, b //",
        "System", "name", "x",
    )

    assert driver.session_obj.queries == []


@pytest.mark.asyncio
async def test_upsert_edge_rejects_unknown_target_label():
    client, driver = _client_with_fake_driver()

    await client.upsert_edge("Decision", "id", 1, "AFFECTS", "Attacker", "name", "x")

    assert driver.session_obj.queries == []


@pytest.mark.asyncio
async def test_upsert_edge_accepts_allowlisted_values():
    client, driver = _client_with_fake_driver()

    await client.upsert_edge("Decision", "id", 1, "AFFECTS", "System", "name", "결제 게이트웨이")

    assert len(driver.session_obj.queries) == 1
    assert "[r:AFFECTS]" in driver.session_obj.queries[0]


@pytest.mark.asyncio
async def test_query_subgraph_clamps_max_hops():
    client, driver = _client_with_fake_driver()

    await client.query_subgraph([1], max_hops=999)

    assert len(driver.session_obj.queries) == 1
    assert "*1..5]" in driver.session_obj.queries[0]
    assert "*1..999]" not in driver.session_obj.queries[0]


# ─── 5. GraphSyncService ────────────────────────────────────

@pytest.mark.asyncio
async def test_graph_sync_service():
    mock_client = MagicMock(spec=Neo4jGraphClient)
    mock_client.upsert_decision = AsyncMock()
    mock_client.upsert_node = AsyncMock()
    mock_client.upsert_edge = AsyncMock()
    mock_client.clear_extracted_relations = AsyncMock(return_value=0)

    mock_extractor = MagicMock(spec=DecisionGraphExtractor)
    mock_extractor.extract_graph_elements = AsyncMock(
        return_value={
            "systems": ["결제시스템"],
            "teams": ["인증팀"],
            "relations": [
                {
                    "rel_type": "AFFECTS",
                    "target_type": "System",
                    "target_name": "결제시스템",
                    "description": "세션 변경 영향",
                }
            ],
        }
    )

    sync_service = GraphSyncService(mock_client, mock_extractor)

    # 1. Sync Document
    await sync_service.sync_document(
        doc_id=5,
        title="인증 변경 건",
        content="내용...",
        author_team="인증팀",
    )
    mock_client.upsert_decision.assert_called_once()
    mock_client.upsert_node.assert_any_call("System", "name", "결제시스템")
    mock_client.upsert_edge.assert_called_once()

    # 2. Sync Conflict Relation
    await sync_service.sync_conflict_relation(
        source_doc_id=5,
        target_doc_id=2,
        conflict_type="DIRECT_CONTRADICTION",
        severity_score=0.95,
        reasoning="출근 vs 재택",
    )
    mock_client.upsert_edge.assert_called_with(
        source_label="Decision",
        source_key="id",
        source_val=5,
        rel_type="CONFLICTS_WITH",
        target_label="Decision",
        target_key="id",
        target_val=2,
        properties={
            "conflict_type": "DIRECT_CONTRADICTION",
            "severity": 0.95,
            "reasoning": "출근 vs 재택",
        },
    )


@pytest.mark.asyncio
async def test_delete_decision_detaches_the_document_node():
    client, driver = _client_with_fake_driver()

    assert await client.delete_decision(42) == 0  # 가짜 드라이버는 결과 행이 없다

    [query] = driver.session_obj.queries
    assert "MATCH (d:Decision {id: $id})" in query and "DETACH DELETE d" in query


@pytest.mark.asyncio
async def test_delete_decision_without_connection_is_a_no_op():
    assert await Neo4jGraphClient("bolt://unused", "u", "p").delete_decision(42) == 0


@pytest.mark.asyncio
async def test_edge_to_existing_node_only_uses_match_for_target():
    client, driver = _client_with_fake_driver()

    await client.upsert_edge("Decision", "id", 1, "SUPERSEDES", "Decision", "id", 51, create_target=False)
    await client.upsert_edge("Decision", "id", 1, "AFFECTS", "System", "name", "결제", {})

    match_only, merge = driver.session_obj.queries
    assert "MATCH (b:Decision {id: $t_val})" in match_only and "MERGE (b:" not in match_only
    assert "MERGE (b:System {name: $t_val})" in merge


def _sync_with(elements_by_call):
    client = MagicMock(spec=Neo4jGraphClient)
    client.upsert_decision = AsyncMock()
    client.upsert_node = AsyncMock()
    client.upsert_edge = AsyncMock()
    client.clear_extracted_relations = AsyncMock(return_value=0)
    extractor = MagicMock(spec=DecisionGraphExtractor)
    extractor.extract_graph_elements = AsyncMock(side_effect=elements_by_call)
    return GraphSyncService(client, extractor), client, extractor


@pytest.mark.asyncio
async def test_sync_document_does_not_recreate_decision_targets_by_id():
    sync, client, _ = _sync_with([{
        "systems": [], "teams": [],
        "relations": [
            {"rel_type": "SUPERSEDES", "target_type": "Decision", "target_name": "51"},
            {"rel_type": "SUPERSEDES", "target_type": "Decision", "target_name": "옛 결제 정책"},
        ],
    }])

    await sync.sync_document(doc_id=5, title="개정안", content="본문")

    by_target = {c.kwargs["target_val"]: c.kwargs["create_target"] for c in client.upsert_edge.await_args_list}
    assert by_target == {51: False, "옛 결제 정책": True}


@pytest.mark.asyncio
async def test_sync_document_extracts_each_source_and_merges():
    sync, client, extractor = _sync_with([
        {"systems": ["결제시스템"], "teams": ["결제팀"],
         "relations": [{"rel_type": "AFFECTS", "target_type": "System", "target_name": "결제시스템"}]},
        {"systems": ["결제시스템", "정산서버"], "teams": [],
         "relations": [{"rel_type": "AFFECTS", "target_type": "System", "target_name": "결제시스템"},
                       {"rel_type": "DEPENDS_ON", "target_type": "System", "target_name": "정산서버"}]},
    ])

    await sync.sync_document(
        doc_id=5, title="문서", content="폼 본문 요약", sources=["창 1", "창 2"], extract_max_chars=4000
    )

    assert [c.kwargs["content"] for c in extractor.extract_graph_elements.await_args_list] == ["창 1", "창 2"]
    assert all(c.kwargs["max_chars"] == 4000 for c in extractor.extract_graph_elements.await_args_list)
    assert client.upsert_decision.await_args.kwargs["summary"] == "폼 본문 요약"
    assert [c.args for c in client.upsert_node.await_args_list] == [
        ("System", "name", "결제시스템"), ("System", "name", "정산서버"), ("Team", "name", "결제팀"),
    ]
    assert [c.kwargs["target_val"] for c in client.upsert_edge.await_args_list] == ["결제시스템", "정산서버"]



@pytest.mark.asyncio
async def test_clear_extracted_relations_keeps_conflict_edges():
    client, driver = _client_with_fake_driver()

    await client.clear_extracted_relations(5)

    [query] = driver.session_obj.queries
    assert "MATCH (d:Decision {id: $id})-[r]->()" in query and "type(r) IN $types" in query
    from history_shared.graph.schema import EXTRACTED_REL_TYPES
    assert "CONFLICTS_WITH" not in EXTRACTED_REL_TYPES and "AFFECTS" in EXTRACTED_REL_TYPES


@pytest.mark.asyncio
async def test_resync_clears_previous_relations_before_writing_new_ones():
    sync, client, _ = _sync_with([{"systems": ["결제시스템"], "teams": [],
                                   "relations": [{"rel_type": "AFFECTS", "target_type": "System", "target_name": "결제시스템"}]}])
    calls = []
    client.clear_extracted_relations.side_effect = lambda doc_id: calls.append(("clear", doc_id)) or 2
    client.upsert_edge.side_effect = lambda **kw: calls.append(("edge", kw["target_val"]))

    await sync.sync_document(doc_id=5, title="개정판", content="본문")

    assert calls == [("clear", 5), ("edge", "결제시스템")]


@pytest.mark.asyncio
async def test_extractor_does_not_fall_back_to_regex_when_llm_is_unavailable():
    from history_shared.llm import LlmUnavailableError

    generator = MagicMock()
    generator.complete = AsyncMock(side_effect=LlmUnavailableError("429"))

    with pytest.raises(LlmUnavailableError):
        await DecisionGraphExtractor(generator).extract_graph_elements(1, "결제 시스템 개편", "결제시스템을 바꾼다")

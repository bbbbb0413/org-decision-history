"""Neo4j 비동기 지식 그래프 클라이언트 (Async Neo4j Client)."""
from typing import Any, Dict, List, Optional
import structlog
from neo4j import AsyncGraphDatabase, AsyncDriver

from .models import GraphEdge, GraphNode, SubgraphResult
from .schema import EXTRACTED_REL_TYPES, KEY_FIELDS, NODE_LABELS, REL_TYPES

log = structlog.get_logger()


class Neo4jGraphClient:
    def __init__(self, uri: str, user: str, password: str):
        self._uri = uri
        self._user = user
        self._password = password
        self._driver: Optional[AsyncDriver] = None

    async def connect(self) -> "Neo4jGraphClient":
        try:
            self._driver = AsyncGraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
                max_connection_lifetime=3600,
            )
            await self._driver.verify_connectivity()
            log.info("Neo4j Graph DB 연결 성공", uri=self._uri)
            await self.init_schema()
        except Exception as e:
            log.warning("Neo4j 연결 실패 (GraphRAG 비활성화 또는 폴백 동작)", error=str(e))
            self._driver = None
        return self

    async def init_schema(self) -> None:
        if not self._driver:
            return
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Decision) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Team) REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:System) REQUIRE s.name IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Policy) REQUIRE p.title IS UNIQUE",
        ]
        async with self._driver.session() as session:
            for c in constraints:
                try:
                    await session.run(c)
                except Exception as e:
                    log.warning("Neo4j 스키마 제약조건 생성 경고", error=str(e))

    async def upsert_decision(
        self,
        doc_id: int,
        title: str,
        status: str = "CURRENT",
        authored_at: str = "",
        summary: str = "",
        organization_id: int = 1,
    ) -> None:
        if not self._driver:
            return
        query = """
        MERGE (d:Decision {id: $id})
        SET d.title = $title,
            d.status = $status,
            d.authored_at = $authored_at,
            d.summary = $summary,
            d.organization_id = $organization_id
        """
        async with self._driver.session() as session:
            await session.run(
                query,
                id=doc_id,
                title=title,
                status=status,
                authored_at=authored_at,
                summary=summary,
                organization_id=organization_id,
            )

    async def delete_decision(self, doc_id: int) -> int:
        """문서 노드와 그 노드에 걸린 관계를 모두 지운다. 지운 노드 수(0 또는 1)를 돌려준다. 없으면 0 — 멱등이다."""
        if not self._driver:
            return 0
        query = """
        MATCH (d:Decision {id: $id})
        DETACH DELETE d
        RETURN count(d) AS removed
        """
        removed = 0
        async with self._driver.session() as session:
            result = await session.run(query, id=doc_id)
            async for record in result:
                removed = int(record["removed"])
        return removed

    async def clear_extracted_relations(self, doc_id: int) -> int:
        """문서에서 나가는 관계 중 본문 추출로 만든 것(EXTRACTED_REL_TYPES)을 지운다. 지운 관계 수를 돌려준다.

        다시 동기화하기 전에 부른다. MERGE 만 하면 개정으로 사라진 관계가 그래프에 남는다.
        상충 관계(CONFLICTS_WITH)는 본문 추출이 아니라 상충 기록이 만들므로 건드리지 않는다.
        """
        if not self._driver:
            return 0
        query = """
        MATCH (d:Decision {id: $id})-[r]->()
        WHERE type(r) IN $types
        DELETE r
        RETURN count(r) AS removed
        """
        removed = 0
        async with self._driver.session() as session:
            result = await session.run(query, id=doc_id, types=sorted(EXTRACTED_REL_TYPES))
            async for record in result:
                removed = int(record["removed"])
        return removed

    async def upsert_node(self, label: str, key_field: str, key_value: Any, properties: Optional[Dict[str, Any]] = None) -> None:
        if label not in NODE_LABELS or key_field not in KEY_FIELDS:
            log.warning("허용되지 않은 라벨·키 필드라 노드 upsert를 건너뜀", label=label, key_field=key_field)
            return
        if not self._driver:
            return
        props = properties or {}
        # 라벨·키 필드는 허용 목록으로 검증했다. Cypher는 이 둘을 파라미터로 바인딩할
        # 수 없어 문자열로 넣어야 하므로, 검증 없이 여기 도달하면 Cypher Injection이 된다
        query = f"""
        MERGE (n:{label} {{{key_field}: $key_value}})
        SET n += $props
        """
        async with self._driver.session() as session:
            await session.run(query, key_value=key_value, props=props)

    async def upsert_edge(
        self,
        source_label: str,
        source_key: str,
        source_val: Any,
        rel_type: str,
        target_label: str,
        target_key: str,
        target_val: Any,
        properties: Optional[Dict[str, Any]] = None,
        create_target: bool = True,
    ) -> None:
        """관계를 만든다. create_target=False 면 대상 노드가 이미 있을 때만 잇고, 없으면 아무것도 만들지 않는다.

        LLM 이 뽑은 문서 id 를 대상으로 걸 때 쓴다 — MERGE 로 만들면 삭제돼 지운 문서 노드가 제목 없이 되살아난다.
        """
        if (
            source_label not in NODE_LABELS
            or target_label not in NODE_LABELS
            or source_key not in KEY_FIELDS
            or target_key not in KEY_FIELDS
            or rel_type not in REL_TYPES
        ):
            log.warning(
                "허용되지 않은 라벨·키 필드·관계 타입이라 엣지 upsert를 건너뜀",
                source_label=source_label, target_label=target_label, rel_type=rel_type,
            )
            return
        if not self._driver:
            return
        props = properties or {}
        target_clause = "MERGE" if create_target else "MATCH"
        query = f"""
        MERGE (a:{source_label} {{{source_key}: $s_val}})
        WITH a
        {target_clause} (b:{target_label} {{{target_key}: $t_val}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $props
        """
        async with self._driver.session() as session:
            await session.run(query, s_val=source_val, t_val=target_val, props=props)

    async def query_subgraph(
        self,
        seed_decision_ids: List[int],
        max_hops: int = 2,
        limit: int = 25,
    ) -> SubgraphResult:
        """시드 의사결정 ID들로부터 1~2-hop 연결된 인과 관계 및 종속 서브그래프를 조회한다."""
        if not self._driver or not seed_decision_ids:
            return SubgraphResult()

        # max_hops는 지금 모든 호출부가 내부 설정값만 넘겨 공격 표면이 아니지만,
        # f-string으로 Cypher에 들어가는 값이라 앞으로도 그렇다는 걸 보장하기 위해 범위를 죈다
        safe_hops = max(1, min(int(max_hops), 5))
        query = f"""
        MATCH (d:Decision)
        WHERE d.id IN $seed_ids
        MATCH (d)-[r:AFFECTS|DEPRECATES|SUPERSEDES|DEPENDS_ON|DECIDED_BY|IMPLEMENTS*1..{safe_hops}]-(target)
        WHERE target <> d
        WITH d, r, target
        LIMIT $limit
        RETURN d, r, target
        """
        triples: List[str] = []
        nodes_map: Dict[str, GraphNode] = {}
        edges: List[GraphEdge] = []

        try:
            async with self._driver.session() as session:
                result = await session.run(query, seed_ids=seed_decision_ids, limit=limit)
                async for record in result:
                    d_node = record["d"]
                    rel_list = record["r"]
                    target_node = record["target"]

                    d_title = d_node.get("title", f"문서 #{d_node.get('id')}")
                    target_labels = list(target_node.labels) if hasattr(target_node, "labels") else ["Entity"]
                    target_label = target_labels[0] if target_labels else "Entity"
                    target_name = target_node.get("name") or target_node.get("title") or f"ID:{target_node.get('id', '')}"

                    for rel in rel_list:
                        rel_type = rel.type
                        props = dict(rel)
                        detail = f" ({props['description']})" if "description" in props else ""
                        if "reasoning" in props:
                            detail = f" (사유: {props['reasoning']})"

                        triple_str = f"[{d_title}] ──[{rel_type}{detail}]──> [{target_label}: {target_name}]"
                        if triple_str not in triples:
                            triples.append(triple_str)

                        edges.append(
                            GraphEdge(
                                source_label="Decision",
                                source_id=d_node.get("id"),
                                rel_type=rel_type,
                                target_label=target_label,
                                target_id=target_name,
                                properties=props,
                            )
                        )
        except Exception as e:
            log.warning("지식 서브그래프 조회 실패", error=str(e))

        return SubgraphResult(triples=triples, edges=edges)

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

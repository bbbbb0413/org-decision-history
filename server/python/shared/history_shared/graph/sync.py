"""의사결정 지식 그래프 동기화 서비스 (Graph Sync Service)."""
from typing import Optional
import structlog
from .client import Neo4jGraphClient
from .extractor import DecisionGraphExtractor

log = structlog.get_logger()


class GraphSyncService:
    def __init__(self, client: Neo4jGraphClient, extractor: DecisionGraphExtractor):
        self._client = client
        self._extractor = extractor

    async def sync_document(
        self,
        doc_id: int,
        title: str,
        content: str,
        status: str = "CURRENT",
        authored_at: str = "",
        organization_id: int = 1,
        author_team: Optional[str] = None,
    ) -> None:
        """문서 정보를 기반으로 Neo4j 지식 그래프 노드 및 관계를 추출하고 동기화한다."""
        if not title:
            return

        # 1. 의사결정(Decision) 노드 등록
        await self._client.upsert_decision(
            doc_id=doc_id,
            title=title,
            status=status,
            authored_at=authored_at,
            summary=content[:200] if content else "",
            organization_id=organization_id,
        )

        if not content or not content.strip():
            return

        # 2. LLM 기반 엔티티 및 인과 관계 추출
        elements = await self._extractor.extract_graph_elements(
            doc_id=doc_id,
            title=title,
            content=content,
            author_team=author_team,
        )

        systems = elements.get("systems", [])
        teams = elements.get("teams", [])
        relations = elements.get("relations", [])

        # 3. 대상 노드들 색인
        for s in systems:
            await self._client.upsert_node("System", "name", s)

        for t in teams:
            await self._client.upsert_node("Team", "name", t)

        # 4. 관계(Edge) 연결
        for r in relations:
            target_type = r.get("target_type", "System")
            target_name = r.get("target_name")
            rel_type = r.get("rel_type", "AFFECTS")
            desc = r.get("description", "")

            if not target_name:
                continue

            target_key = "id" if target_type == "Decision" and str(target_name).isdigit() else "name"
            target_val = int(target_name) if target_key == "id" else target_name

            await self._client.upsert_edge(
                source_label="Decision",
                source_key="id",
                source_val=doc_id,
                rel_type=rel_type,
                target_label=target_type,
                target_key=target_key,
                target_val=target_val,
                properties={"description": desc} if desc else {},
            )

        log.info(
            "Neo4j 지식 그래프 동기화 완료",
            doc_id=doc_id,
            systems=len(systems),
            teams=len(teams),
            relations=len(relations),
        )

    async def sync_conflict_relation(
        self,
        source_doc_id: int,
        target_doc_id: int,
        conflict_type: str,
        severity_score: float,
        reasoning: str,
    ) -> None:
        """감지된 상충(Conflict)을 지식 그래프 상에 상호 배타적 관계로 기록한다."""
        await self._client.upsert_edge(
            source_label="Decision",
            source_key="id",
            source_val=source_doc_id,
            rel_type="CONFLICTS_WITH",
            target_label="Decision",
            target_key="id",
            target_val=target_doc_id,
            properties={
                "conflict_type": conflict_type,
                "severity": float(severity_score),
                "reasoning": reasoning,
            },
        )
        log.info(
            "지식 그래프 상충 관계(CONFLICTS_WITH) 반영 완료",
            source_doc_id=source_doc_id,
            target_doc_id=target_doc_id,
            conflict_type=conflict_type,
            severity=severity_score,
        )

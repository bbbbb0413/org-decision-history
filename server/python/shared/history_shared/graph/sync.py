"""의사결정 지식 그래프 동기화 서비스 (Graph Sync Service)."""
from collections.abc import Sequence
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
        sources: Optional[Sequence[str]] = None,
        extract_max_chars: int = 3000,
    ) -> None:
        """문서 정보를 기반으로 Neo4j 지식 그래프 노드 및 관계를 추출하고 동기화한다.

        sources 를 주면 각 조각에서 따로 추출해 합친다(governance-worker 는 상충 검사와 같은 색인 청크 창을 넘긴다 —
        원본 파일을 올린 문서도 파일 내용으로 그래프를 만든다). 없으면 content 하나에서 추출한다.
        content 는 Decision 노드 요약(앞 200자)에도 쓴다.
        """
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

        pieces = [p for p in (sources if sources is not None else [content]) if p and p.strip()]
        if not pieces:
            return

        # 2. LLM 기반 엔티티 및 인과 관계 추출. 조각마다 뽑아 겹치는 것은 한 번만 쓴다
        systems: list[str] = []
        teams: list[str] = []
        relations: list[dict] = []
        seen_relations: set[tuple] = set()
        for piece in pieces:
            elements = await self._extractor.extract_graph_elements(
                doc_id=doc_id,
                title=title,
                content=piece,
                author_team=author_team,
                max_chars=extract_max_chars,
            )
            systems += [x for x in elements.get("systems", []) if x not in systems]
            teams += [x for x in elements.get("teams", []) if x not in teams]
            for r in elements.get("relations", []):
                key = (r.get("rel_type"), r.get("target_type"), r.get("target_name"))
                if key not in seen_relations:
                    seen_relations.add(key)
                    relations.append(r)

        # 이전 동기화에서 본문 추출로 만든 관계를 걷어낸다. 개정으로 사라진 관계가 남지 않게 한다.
        # 추출을 다 끝낸 뒤에 지워, 추출 도중 실패해도 기존 관계가 비지 않는다
        cleared = await self._client.clear_extracted_relations(doc_id)

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
            # 다른 문서를 id 로 가리키는 관계는 그 문서 노드가 있을 때만 잇는다. 삭제돼 지운 노드를
            # MERGE 로 되살리면 제목 없는 유령 노드가 GraphRAG 문맥에 실린다
            create_target = target_key != "id"

            await self._client.upsert_edge(
                source_label="Decision",
                source_key="id",
                source_val=doc_id,
                rel_type=rel_type,
                target_label=target_type,
                target_key=target_key,
                target_val=target_val,
                properties={"description": desc} if desc else {},
                create_target=create_target,
            )

        log.info(
            "Neo4j 지식 그래프 동기화 완료",
            doc_id=doc_id,
            sources=len(pieces),
            cleared=cleared,
            systems=len(systems),
            teams=len(teams),
            relations=len(relations),
        )

    async def remove_document(self, doc_id: int) -> int:
        """삭제된 문서를 그래프에서 뺀다. 이 문서에서 나가고 들어오는 관계(상충 포함)도 함께 사라진다."""
        return await self._client.delete_decision(doc_id)

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

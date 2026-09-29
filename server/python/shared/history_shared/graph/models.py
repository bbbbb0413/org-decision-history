"""GraphRAG 데이터 모델."""
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class GraphNode:
    id: str | int
    label: str  # "Decision", "Team", "System", "Policy"
    name: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source_label: str
    source_id: str | int
    rel_type: str  # "AFFECTS", "DEPRECATES", "SUPERSEDES", "DEPENDS_ON", "DECIDED_BY", "IMPLEMENTS"
    target_label: str
    target_id: str | int
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalPath:
    elements: List[str]  # e.g. ["[결정 #17]", "-(DEPRECATES)->", "[결정 #16]"]

    def to_str(self) -> str:
        return " ".join(self.elements)


@dataclass
class SubgraphResult:
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)
    triples: List[str] = field(default_factory=list)

    def to_context_str(self) -> str:
        """LLM 프롬프트에 주입할 지식 서브그래프 인과 텍스트 생성."""
        if not self.triples:
            return ""
        lines = [
            "[사내 의사결정 인과 관계망 (Knowledge Subgraph)]:",
        ]
        for triple in self.triples:
            lines.append(f"- {triple}")
        return "\n".join(lines)

"""StateGraph 연결. 보안 가드레일 -> 권한 스코프 -> 질의 재작성 -> 질의 지능화(HyDE/분해) -> 검색 -> 생성 -> 자가 비판(Critique)."""
from langgraph.graph import END, StateGraph

from app.graph.nodes import GraphNodes
from app.graph.state import AnswerState


def build_graph(nodes: GraphNodes):
    graph = StateGraph(AnswerState)

    # 1. 보안 가드레일 (PII 마스킹 및 인젝션 차단)
    graph.add_node("guardrail", nodes.guardrail)
    graph.add_node("security_blocked", nodes.security_blocked)

    # 2. 질의 전처리 및 지능화
    graph.add_node("fetch_scope", nodes.fetch_scope)
    graph.add_node("rewrite_question", nodes.rewrite_question)
    graph.add_node("expand_queries", nodes.expand_queries)

    # 3. 검색 및 근거 검증
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("rerank", nodes.rerank)
    graph.add_node("no_evidence_no_hits", nodes.no_evidence_no_hits)
    graph.add_node("resolve_sources", nodes.resolve_sources)
    graph.add_node("no_evidence_no_sources", nodes.no_evidence_no_sources)
    graph.add_node("check_cache", nodes.check_cache)
    graph.add_node("emit_cached", nodes.emit_cached)

    # 4. 답변 생성 및 자가 비판 (Self-Critique & Refinement Loop)
    graph.add_node("generate", nodes.generate)
    graph.add_node("critique", nodes.critique)
    graph.add_node("refine_query", nodes.refine_query)

    # 5. 저장 및 완료
    graph.add_node("persist_answer", nodes.persist_answer)
    graph.add_node("refresh_summary_and_finish", nodes.refresh_summary_and_finish)

    # ──────────────── 엣지 연결 ────────────────
    # 엔트리 포인트: 보안 가드레일
    graph.set_entry_point("guardrail")
    graph.add_conditional_edges(
        "guardrail",
        nodes.route_after_guardrail,
        {"fetch_scope": "fetch_scope", "security_blocked": "security_blocked"},
    )
    graph.add_edge("security_blocked", END)

    # 권한 스코프 -> 질의 재작성 -> 질의 지능화(HyDE/분해) -> 검색 -> 재순위화
    graph.add_edge("fetch_scope", "rewrite_question")
    graph.add_edge("rewrite_question", "expand_queries")
    graph.add_edge("expand_queries", "retrieve")
    graph.add_edge("retrieve", "rerank")

    # 재순위화 후 근거 판정
    graph.add_conditional_edges(
        "rerank",
        nodes.route_after_rerank,
        {"resolve_sources": "resolve_sources", "no_evidence_no_hits": "no_evidence_no_hits"},
    )
    graph.add_edge("no_evidence_no_hits", END)

    # 출처 권한 재확인
    graph.add_conditional_edges(
        "resolve_sources",
        nodes.route_after_sources,
        {"check_cache": "check_cache", "no_evidence_no_sources": "no_evidence_no_sources"},
    )
    graph.add_edge("no_evidence_no_sources", END)

    # 캐시 확인
    graph.add_conditional_edges(
        "check_cache", nodes.route_after_cache, {"emit_cached": "emit_cached", "generate": "generate"}
    )
    graph.add_edge("emit_cached", END)

    # 생성 -> 자가 비판 (Critique)
    graph.add_edge("generate", "critique")

    # 자가 비판 결과에 따른 조건부 분기 (재검색 vs 저장)
    graph.add_conditional_edges(
        "critique",
        nodes.route_after_critique,
        {"refine_query": "refine_query", "persist_answer": "persist_answer"},
    )
    # 재검색 루프: refine_query -> retrieve
    graph.add_edge("refine_query", "retrieve")

    # 저장 -> 요약 갱신 및 완료
    graph.add_edge("persist_answer", "refresh_summary_and_finish")
    graph.add_edge("refresh_summary_and_finish", END)

    return graph.compile()

"""답변 파이프라인의 LangGraph 노드.

흐름: 가드레일 → 권한 스코프 → 질의 재작성·확장 → 검색 → 재순위화 → 출처 권한 재확인·GraphRAG
→ 캐시 → 생성 → 자가비판(신뢰도가 낮으면 1회 재검색). 연결은 build.py 에 있다.
"""
import re
import time

import structlog
from langgraph.types import StreamWriter

from app.answer_cache import AnswerCache
from app.auth_client import AuthClient
from app.db import Db
from app.generation.base import AnswerGenerator
from app.generation.critique import CritiqueGenerator
from app.generation.models import AnswerChunk, AnswerRequest, AnswerStatus
from app.generation.prompt import DEFAULT_EVIDENCE_COUNT, trim_evidence
from app.graph.state import AnswerState
from app.memory import ConversationMemory
from app.search.complexity_router import QueryComplexityRouter
from app.search.decomposer import QueryDecomposer
from app.search.hyde import HydeService
from app.search.hybrid import HybridSearchService
from app.search.relevance_gate import has_evidence
from app.search.reranker import CrossEncoderReranker
from app.search.source_resolver import SourceResolver
from history_shared.graph.client import Neo4jGraphClient
from app.security.content_validator import ContentValidator
from app.security.pii_scanner import SecretPiiScanner

log = structlog.get_logger()

_SUGGEST_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)


def _emit(writer: StreamWriter, event: str, data: dict) -> None:
    writer({"event": event, "data": data})


def _elapsed_ms(state: AnswerState) -> int:
    return int((time.monotonic() - state["started_at"]) * 1000)


def _suggest(question: str) -> list[str]:
    """근거를 못 찾았을 때 줄 검색어 제안. 질문의 낱말을 줄여 더 넓게 찾도록 유도한다."""
    cleaned = _SUGGEST_PATTERN.sub(" ", question).strip()
    return [t for t in cleaned.split() if len(t) >= 2][:3]


class GraphNodes:
    def __init__(
        self,
        db: Db,
        auth: AuthClient,
        hybrid_search: HybridSearchService,
        source_resolver: SourceResolver,
        answer_cache: AnswerCache,
        memory: ConversationMemory,
        generator: AnswerGenerator,
        pii_scanner: SecretPiiScanner | None = None,
        content_validator: ContentValidator | None = None,
        complexity_router: QueryComplexityRouter | None = None,
        decomposer: QueryDecomposer | None = None,
        hyde: HydeService | None = None,
        critique: CritiqueGenerator | None = None,
        reranker: CrossEncoderReranker | None = None,
        candidate_count: int = 20,
        reranker_top_k: int = DEFAULT_EVIDENCE_COUNT,
        reranker_min_score: float = 0.0,
        graph_client: Neo4jGraphClient | None = None,
        graphrag_enabled: bool = True,
        graphrag_max_hops: int = 2,
    ):
        self._db = db
        self._auth = auth
        self._hybrid_search = hybrid_search
        self._source_resolver = source_resolver
        self._answer_cache = answer_cache
        self._memory = memory
        self._generator = generator
        self._pii_scanner = pii_scanner or SecretPiiScanner()
        self._content_validator = content_validator or ContentValidator()
        self._complexity_router = complexity_router or QueryComplexityRouter()
        self._decomposer = decomposer or QueryDecomposer(generator)
        self._hyde = hyde or HydeService(generator)
        self._critique = critique or CritiqueGenerator(generator)
        self._reranker = reranker or CrossEncoderReranker(enabled=False)
        self._candidate_count = candidate_count
        self._reranker_top_k = reranker_top_k
        self._reranker_min_score = reranker_min_score
        self._graph_client = graph_client
        self._graphrag_enabled = graphrag_enabled
        self._graphrag_max_hops = graphrag_max_hops

    async def guardrail(self, state: AnswerState) -> dict:
        """1단계: PII 마스킹 및 프롬프트 인젝션 검사."""
        raw_question = state["question"]
        masked = self._pii_scanner.mask(raw_question)
        verdict = self._content_validator.inspect(masked)

        if not verdict.is_allowed:
            log.warning(
                "보안 가드레일 차단",
                conversation_id=state.get("conversation_id"),
                reason=verdict.reason,
                pattern=verdict.pattern,
            )
            return {
                "masked_question": masked,
                "question": masked,
                "security_verdict": "BLOCKED",
                "block_reason": verdict.reason,
            }

        return {
            "masked_question": masked,
            "question": masked,
            "security_verdict": "ALLOWED",
            "block_reason": None,
        }

    def route_after_guardrail(self, state: AnswerState) -> str:
        if state.get("security_verdict") == "BLOCKED":
            return "security_blocked"
        return "fetch_scope"

    async def security_blocked(self, state: AnswerState, writer: StreamWriter) -> dict:
        """보안 정책 위반 시 즉시 종료."""
        reason = state.get("block_reason") or "보안 정책 위반"
        notice = f"입력하신 질문에서 {reason}이 감지되어 답변을 생성할 수 없습니다."
        _emit(writer, "sources", {"sources": []})
        _emit(writer, "delta", {"text": f"{notice}\n"})

        message_id = await self._db.append_assistant_message(
            state["conversation_id"],
            notice,
            "guardrail",
            AnswerStatus.DEGRADED.value,
            _elapsed_ms(state),
            [],
        )
        self._emit_done(writer, message_id, AnswerStatus.DEGRADED, "guardrail", _elapsed_ms(state), [], None)
        return {"message_id": message_id, "finished": True}

    async def fetch_scope(self, state: AnswerState) -> dict:
        scope = await self._auth.scope_of(state["user_id"])
        await self._auth.record_audit(
            state["user_id"],
            "DOCUMENT_SEARCH",
            "CONVERSATION",
            state["conversation_id"],
            not scope.is_empty(),
            "열람 가능한 문서가 없어 검색을 생략함" if scope.is_empty() else "질의응답 검색 실행",
        )
        return {"scope": scope}

    async def rewrite_question(self, state: AnswerState) -> dict:
        rewritten = await self._memory.rewrite_question(state["conversation_id"], state["question"])
        await self._db.append_user_message(state["conversation_id"], state["question"], rewritten)
        await self._db.rename_if_untitled(state["conversation_id"], state["question"])
        return {"rewritten_question": rewritten}

    async def expand_queries(self, state: AnswerState) -> dict:
        """질문 복잡도 분석 및 검색 쿼리 지능화 (HyDE, Decomposition)."""
        rewritten = state["rewritten_question"]
        complexity = self._complexity_router.route(rewritten)

        search_queries = [rewritten]
        if complexity == "complex":
            sub_queries = await self._decomposer.decompose(rewritten)
            if sub_queries:
                search_queries = sub_queries
        elif complexity == "hyde":
            hypo = await self._hyde.generate_hypothetical_document(rewritten)
            if hypo:
                search_queries = [rewritten, hypo]

        log.info(
            "질의 지능화 완료",
            complexity=complexity,
            queries_count=len(search_queries),
            queries=search_queries,
        )
        return {"complexity": complexity, "search_queries": search_queries}

    async def retrieve(self, state: AnswerState) -> dict:
        queries = state.get("search_queries") or [state["rewritten_question"]]
        limit = self._candidate_count if self._reranker.enabled else DEFAULT_EVIDENCE_COUNT
        hits = await self._hybrid_search.search_multi(
            queries, state["scope"], limit
        )
        return {"hits": hits}

    async def rerank(self, state: AnswerState) -> dict:
        raw_hits = state.get("hits", [])
        if not raw_hits:
            return {"hits": []}

        reranked = await self._reranker.rerank(
            query=state["rewritten_question"],
            hits=raw_hits,
            top_k=self._reranker_top_k,
            min_score=self._reranker_min_score,
        )
        log.info(
            "2차 재순위화(Rerank) 완료",
            candidate_count=len(raw_hits),
            reranked_count=len(reranked),
            top_score=reranked[0].rerank_score if reranked else None,
        )
        return {"hits": reranked}

    def route_after_rerank(self, state: AnswerState) -> str:
        if has_evidence(state["rewritten_question"], state["hits"]):
            return "resolve_sources"
        return "no_evidence_no_hits"

    def route_after_retrieve(self, state: AnswerState) -> str:
        """기존 라우팅 호환성 유지용 alias."""
        return self.route_after_rerank(state)

    async def no_evidence_no_hits(self, state: AnswerState, writer: StreamWriter) -> dict:
        """근거 자체가 없을 때의 조기 종료 (질문 낱말 겹침이 기준 미달)."""
        _emit(writer, "sources", {"sources": []})
        suggestions = _suggest(state["question"])
        _emit(writer, "delta", {"text": "권한 범위 안에서 근거가 되는 문서를 찾지 못했습니다. 답변을 만들지 않았습니다.\n"})
        message_id = await self._db.append_assistant_message(
            state["conversation_id"],
            "근거를 찾지 못해 답변하지 않았습니다.",
            "none",
            AnswerStatus.NO_EVIDENCE.value,
            _elapsed_ms(state),
            [],
        )
        self._emit_done(writer, message_id, AnswerStatus.NO_EVIDENCE, "none", _elapsed_ms(state), suggestions, None)
        return {"message_id": message_id, "finished": True}

    async def resolve_sources(self, state: AnswerState) -> dict:
        evidence = trim_evidence(state["hits"])
        sources = await self._source_resolver.resolve(evidence, state["scope"])

        subgraph_context = None
        subgraph_triples = []
        subgraph_nodes = []
        subgraph_edges = []
        if self._graph_client and self._graphrag_enabled and evidence:
            try:
                seed_ids = [h.document_id for h in evidence if h.document_id is not None]
                if seed_ids:
                    subgraph = await self._graph_client.query_subgraph(
                        seed_ids,
                        max_hops=self._graphrag_max_hops,
                    )
                    if subgraph:
                        subgraph_triples = subgraph.triples
                        subgraph_context = subgraph.to_context_str()
                        from dataclasses import asdict
                        subgraph_nodes = [asdict(n) for n in subgraph.nodes]
                        subgraph_edges = [asdict(e) for e in subgraph.edges]
                        log.info(
                            "GraphRAG 지식 서브그래프 융합 완료",
                            seed_ids=seed_ids,
                            triples_count=len(subgraph_triples),
                            nodes_count=len(subgraph_nodes),
                            edges_count=len(subgraph_edges),
                        )
            except Exception as e:
                log.warning("GraphRAG 서브그래프 조회 실패 (폴백 진행)", error=str(e))

        return {
            "evidence": evidence,
            "sources": sources,
            "subgraph_context": subgraph_context,
            "subgraph_triples": subgraph_triples,
            "subgraph_nodes": subgraph_nodes,
            "subgraph_edges": subgraph_edges,
        }

    def route_after_sources(self, state: AnswerState) -> str:
        if state["sources"]:
            return "check_cache"
        return "no_evidence_no_sources"

    async def no_evidence_no_sources(self, state: AnswerState, writer: StreamWriter) -> dict:
        """전송 직전 재확인에서 모든 출처가 걸러졌을 때의 조기 종료."""
        _emit(writer, "sources", {"sources": []})
        _emit(writer, "delta", {"text": "근거 문서에 대한 접근 권한이 없어 답변을 만들지 않았습니다.\n"})
        message_id = await self._db.append_assistant_message(
            state["conversation_id"],
            "권한 범위 밖 문서만 검색되어 답변하지 않았습니다.",
            "none",
            AnswerStatus.NO_EVIDENCE.value,
            _elapsed_ms(state),
            [],
        )
        self._emit_done(writer, message_id, AnswerStatus.NO_EVIDENCE, "none", _elapsed_ms(state), [], None)
        return {"message_id": message_id, "finished": True}

    async def check_cache(self, state: AnswerState, writer: StreamWriter) -> dict:
        _emit(writer, "sources", {"sources": state["sources"]})
        if state.get("subgraph_nodes") or state.get("subgraph_edges"):
            _emit(writer, "graph", {
                "nodes": state.get("subgraph_nodes") or [],
                "edges": state.get("subgraph_edges") or [],
            })
        scope_keys = state["scope"].to_scope_keys()
        cached = await self._answer_cache.find(state["rewritten_question"], scope_keys)
        return {"cached_answer": cached}

    def route_after_cache(self, state: AnswerState) -> str:
        if state.get("cached_answer"):
            return "emit_cached"
        return "generate"

    async def emit_cached(self, state: AnswerState, writer: StreamWriter) -> dict:
        answer = state["cached_answer"]
        _emit(writer, "delta", {"text": answer})
        message_id = await self._db.append_assistant_message(
            state["conversation_id"],
            answer,
            "cache",
            AnswerStatus.OK.value,
            _elapsed_ms(state),
            state["evidence"],
        )
        self._emit_done(writer, message_id, AnswerStatus.OK, "cache", _elapsed_ms(state), [], None)
        return {"message_id": message_id, "finished": True}

    async def generate(self, state: AnswerState, writer: StreamWriter) -> dict:
        summary = await self._memory.summary_of(state["conversation_id"])
        recent = await self._db.recent_turns(state["conversation_id"], 4)
        recent_texts = self._memory.recent_turn_texts(recent)

        # GraphRAG 지식 서브그래프 인과 컨텍스트가 있으면 질문에 융합
        question = state["rewritten_question"]
        if state.get("subgraph_context"):
            question = f"{question}\n\n{state['subgraph_context']}"

        request = AnswerRequest(
            question=question,
            summary=summary,
            recent_turns=recent_texts,
            evidence=state["evidence"],
        )

        buffer: list[str] = []
        result: dict = {"status": AnswerStatus.LOW_CONFIDENCE, "model_name": None, "resume_at": None}

        async def on_chunk(chunk: AnswerChunk) -> None:
            if chunk.done:
                result["status"] = chunk.status
                result["model_name"] = chunk.model_name
                result["resume_at"] = chunk.resume_at
                return
            buffer.append(chunk.text or "")
            _emit(writer, "delta", {"text": chunk.text})

        try:
            await self._generator.generate(request, on_chunk)
        except Exception as e:
            log.error("답변 생성 실패", conversation_id=state["conversation_id"], error=str(e))
            result["status"] = AnswerStatus.LOW_CONFIDENCE

        return {
            "answer_text": "".join(buffer),
            "status": result["status"],
            "model_name": result["model_name"],
            "resume_at": result["resume_at"],
        }

    async def critique(self, state: AnswerState) -> dict:
        """Step 3: 생성된 답변과 근거를 대조하여 환각 및 신뢰도 자체 평가."""
        evidence = state.get("evidence") or []
        answer = state.get("answer_text", "")
        question = state.get("rewritten_question", state.get("question", ""))

        result = await self._critique.evaluate(question, answer, evidence)
        log.info(
            "자가 비판 평가 완료",
            confidence=result.confidence,
            answered=result.answered,
            missing=result.missing,
            next_query=result.next_query,
        )
        return {
            "confidence": result.confidence,
            "missing": result.missing,
            "next_query": result.next_query,
        }

    def route_after_critique(self, state: AnswerState) -> str:
        attempts = state.get("critique_attempts", 0)
        confidence = state.get("confidence", 1.0)
        next_query = state.get("next_query")

        # 1회에 한해 신뢰도 0.7 미만이고 보완 쿼리가 있을 때 재검색 루프
        if attempts == 0 and confidence < 0.7 and next_query:
            return "refine_query"
        return "persist_answer"

    async def refine_query(self, state: AnswerState) -> dict:
        """환각/근거 부족 시 쿼리를 보정하고 재검색 준비."""
        attempts = state.get("critique_attempts", 0) + 1
        next_q = state.get("next_query")
        log.info("쿼리 보정보완 후 재검색 진행", next_query=next_q, attempt=attempts)

        search_queries = [next_q] if next_q else [state["rewritten_question"]]
        return {
            "critique_attempts": attempts,
            "search_queries": search_queries,
        }

    async def persist_answer(self, state: AnswerState) -> dict:
        message_id = await self._db.append_assistant_message(
            state["conversation_id"],
            state["answer_text"],
            state["model_name"],
            state["status"].value,
            _elapsed_ms(state),
            state["evidence"],
        )
        if state["status"] in (AnswerStatus.OK, AnswerStatus.LOW_CONFIDENCE):
            await self._answer_cache.put(
                state["rewritten_question"], state["scope"].to_scope_keys(), state["answer_text"]
            )
        return {"message_id": message_id}

    async def refresh_summary_and_finish(self, state: AnswerState, writer: StreamWriter) -> dict:
        await self._memory.refresh_summary(state["conversation_id"])
        self._emit_done(
            writer,
            state["message_id"],
            state["status"],
            state["model_name"],
            _elapsed_ms(state),
            [],
            state.get("resume_at"),
        )
        return {"finished": True}

    def _emit_done(self, writer, message_id, status, model_name, latency_ms, suggestions, resume_at) -> None:
        _emit(
            writer,
            "done",
            {
                "messageId": message_id,
                "answerStatus": status.value,
                "modelName": model_name,
                "latencyMs": latency_ms,
                "suggestions": suggestions,
                "resumeAt": resume_at.isoformat() if resume_at else None,
            },
        )

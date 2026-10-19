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
from app.generation.prompt import DEFAULT_BUDGET, PromptBudget, trim_evidence
from app.graph.state import AnswerState
from app.memory import ConversationMemory
from app.search.hyde import HydeService
from app.search.query_planner import QueryPlan, QueryPlanner
from app.search.hybrid import HybridSearchService
from app.search.evidence_judge import EvidenceJudge
from app.search.relevance_gate import EvidenceGate, has_evidence
from app.search.reranker import CrossEncoderReranker
from app.search.source_resolver import SourceResolver
from history_shared.graph.client import Neo4jGraphClient
from history_shared.metrics import CRITIQUE_CONFIDENCE, EVIDENCE_VERDICTS
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
        query_planner: QueryPlanner | None = None,
        hyde: HydeService | None = None,
        critique: CritiqueGenerator | None = None,
        reranker: CrossEncoderReranker | None = None,
        candidate_count: int = 20,
        reranker_min_score: float = 0.0,
        prompt_budget: PromptBudget = DEFAULT_BUDGET,
        evidence_gate: EvidenceGate = EvidenceGate(),
        critique_min_confidence: float = 0.7,
        graph_client: Neo4jGraphClient | None = None,
        graphrag_enabled: bool = True,
        graphrag_max_hops: int = 2,
        evidence_judge: EvidenceJudge | None = None,
        min_vector_similarity: float | None = 0.62,
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
        # None 이면 질의 계획을 끈 것이다 — 대화 맥락 재작성만 하고 원래 질문 하나로 검색한다
        self._query_planner = query_planner
        self._hyde = hyde or HydeService(generator)
        self._critique = critique or CritiqueGenerator(generator)
        self._reranker = reranker or CrossEncoderReranker(enabled=False)
        self._candidate_count = candidate_count
        self._reranker_min_score = reranker_min_score
        # 근거 수는 리랭커가 남기는 수이자 프롬프트에 넣는 수다 — 한 값(prompt_budget.evidence_count)만 쓴다
        self._prompt_budget = prompt_budget
        self._evidence_gate = evidence_gate
        # 근거 충분성 판정기. None 이면 1차 거름(evidence_gate) 결과만 쓴다
        self._evidence_judge = evidence_judge
        # 근거의 벡터 유사도 최댓값이 이 미만이면 LLM 을 부르지 않고 거절한다.
        # None 이면 유사도가 보정되지 않은 임베딩(대체 해시)이라 유사도로 거르지 못한다 — 대신 리랭커 점수가 있어야 근거로 본다
        self._min_vector_similarity = min_vector_similarity
        # 자가 비판 신뢰도가 이 미만이면 답변 상태를 LOW_CONFIDENCE 로 낮춘다
        self._critique_min_confidence = critique_min_confidence
        self._graph_client = graph_client
        self._graphrag_enabled = graphrag_enabled
        self._graphrag_max_hops = graphrag_max_hops

    async def guardrail(self, state: AnswerState) -> dict:
        """1단계: PII 마스킹 및 프롬프트 인젝션 검사."""
        raw_question = state["question"]
        masked, masked_rules = self._pii_scanner.scan(raw_question)
        if masked_rules:
            # 값은 남기지 않고 걸린 규칙 id 만 남긴다
            log.info("질문의 비밀정보·개인정보를 마스킹했다", conversation_id=state.get("conversation_id"), rule_ids=masked_rules)
        verdict = self._content_validator.inspect(masked)

        if not verdict.is_allowed:
            log.warning(
                "보안 가드레일 차단",
                conversation_id=state.get("conversation_id"),
                reason=verdict.reason,
                rule_id=verdict.rule_id,
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

    async def plan_query(self, state: AnswerState) -> dict:
        """대화 맥락 재작성 + 검색 갈래 + 검색 질의. 질의 계획이 켜져 있으면 경량 모델 한 번으로 다 받는다."""
        conversation_id, question = state["conversation_id"], state["question"]
        if self._query_planner is None:
            plan = QueryPlan(await self._memory.rewrite_question(conversation_id, question))
        else:
            plan = await self._query_planner.plan(question, await self._memory.context_of(conversation_id))
        await self._db.append_user_message(conversation_id, question, plan.query)
        await self._db.rename_if_untitled(conversation_id, question)
        return {"rewritten_question": plan.query, **await self.search_queries_for(plan)}

    async def search_queries_for(self, plan: QueryPlan) -> dict:
        """계획의 갈래대로 검색 질의를 만든다. complex 는 하위 질의, abstract 는 [질의, 가상 문서]."""
        search_queries = [plan.query]
        if plan.kind == "complex":
            search_queries = list(plan.sub_queries)
        elif plan.kind == "abstract":
            hypo = await self._hyde.generate_hypothetical_document(plan.query)
            if hypo:
                search_queries = [plan.query, hypo]

        log.info("검색 질의 계획 완료", kind=plan.kind, queries_count=len(search_queries), queries=search_queries)
        return {"complexity": plan.kind, "search_queries": search_queries}

    async def retrieve(self, state: AnswerState) -> dict:
        queries = state.get("search_queries") or [state["rewritten_question"]]
        limit = self._candidate_count if self._reranker.enabled else self._prompt_budget.evidence_count
        hits = await self._hybrid_search.search_multi(
            queries, state["scope"], limit
        )
        return {"hits": hits}

    async def rerank(self, state: AnswerState) -> dict:
        raw_hits = state.get("hits", [])
        if not raw_hits:
            return {"hits": []}

        queries = state.get("search_queries") or []
        if state.get("complexity") == "complex" and len(queries) > 1:
            # 두 대상을 함께 묻는 질문: 대상마다 따로 매겨 근거 자리를 나눈다
            reranked = await self._reranker.rerank_each(
                queries, raw_hits, top_k=self._prompt_budget.evidence_count, min_score=self._reranker_min_score
            )
        else:
            reranked = await self._reranker.rerank(
                query=state["rewritten_question"],
                hits=raw_hits,
                top_k=self._prompt_budget.evidence_count,
                min_score=self._reranker_min_score,
            )
        log.info(
            "2차 재순위화(Rerank) 완료",
            candidate_count=len(raw_hits),
            reranked_count=len(reranked),
            top_score=reranked[0].rerank_score if reranked else None,
        )
        return {"hits": reranked}

    async def judge_evidence(self, state: AnswerState) -> dict:
        """근거 판정. 벡터 유사도가 낮으면 거절 → 경량 LLM 판정 → (판정 불가면) 낱말·점수 1차 거름.

        리랭커 점수로 판정을 건너뛰지 않는다. 리랭커는 주제가 맞는지를 보지 답이 있는지를 보지 않는다 —
        "도입 후 실제 실패율"처럼 문서에 목표치만 있는 질문도 0.96 을 받았다.
        낱말 겹침도 먼저 쓰지 않는다. 사용자 말투 질문은 정답 청크를 찾고도 낱말이 달라 거절됐다.
        """
        question, hits = state["rewritten_question"], state.get("hits", [])
        similarities = [h.vector_similarity for h in hits if h.vector_similarity is not None]
        top_similarity = max(similarities, default=None)
        floor = self._min_vector_similarity
        if not hits or (floor is not None and top_similarity is not None and top_similarity < floor):
            outcome = "low_similarity"
        elif floor is None and all(h.rerank_score is None for h in hits):
            # 유사도도 리랭커 점수도 믿을 신호가 없다(대체 임베딩 + 리랭커 실패). 낱말 겹침만으로는 관계없는 질문이
            # 흔한 낱말('절차'·'변경')로 통과하므로 답하지 않는다
            outcome = "unscored"
        elif self._evidence_judge is None:
            outcome = "gate_passed" if has_evidence(question, hits, self._evidence_gate) else "gate_rejected"
        else:
            judgement = await self._evidence_judge.judge(question, hits)
            if judgement.evaluated:
                outcome = "judged_answerable" if judgement.answerable else "judged_unanswerable"
            else:
                # 판정 실패(한도 초과 등)는 1차 거름으로 정한다
                passed = has_evidence(question, hits, self._evidence_gate)
                outcome = "unjudged_gate_passed" if passed else "unjudged_gate_rejected"
        log.info("근거 충분성 판정", outcome=outcome, top_similarity=top_similarity)
        EVIDENCE_VERDICTS.labels(outcome=outcome).inc()
        passed = outcome in ("judged_answerable", "gate_passed", "unjudged_gate_passed")
        return {"evidence_verdict": "pass" if passed else outcome, "evidence_outcome": outcome}

    def route_after_judge(self, state: AnswerState) -> str:
        if state.get("evidence_verdict") == "pass":
            return "resolve_sources"
        return "no_evidence_no_hits"

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
        evidence = trim_evidence(state["hits"], self._prompt_budget)
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
        recent = await self._db.recent_turns(state["conversation_id"], self._memory.recent_turns)
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
        """생성된 답변을 근거와 대조해 신뢰도를 매긴다. 낮으면 상태를 LOW_CONFIDENCE 로 낮춘다.

        답변은 이미 스트리밍으로 나갔으므로 여기서 다시 검색·생성하지 않는다. 그러면 화면에
        두 답변이 이어 붙고 출처 번호가 어긋난다. 평가는 신뢰도 표시(배지)로만 쓴다.
        OK 가 아닌 답변(근거 발췌·한도 초과·장애)은 이미 상태가 그 사실을 말하므로 평가하지 않는다.
        """
        if state.get("status") != AnswerStatus.OK:
            return {}

        evidence = state.get("evidence") or []
        answer = state.get("answer_text", "")
        question = state.get("rewritten_question", state.get("question", ""))

        result = await self._critique.evaluate(question, answer, evidence)
        if not result.evaluated:
            return {}

        CRITIQUE_CONFIDENCE.observe(result.confidence)
        low = result.confidence < self._critique_min_confidence
        log.info(
            "자가 비판 평가 완료",
            confidence=result.confidence,
            answered=result.answered,
            missing=result.missing,
            downgraded=low,
        )
        update: dict = {"confidence": result.confidence, "missing": result.missing}
        if low:
            update["status"] = AnswerStatus.LOW_CONFIDENCE
        return update

    async def persist_answer(self, state: AnswerState) -> dict:
        message_id = await self._db.append_assistant_message(
            state["conversation_id"],
            state["answer_text"],
            state["model_name"],
            state["status"].value,
            _elapsed_ms(state),
            state["evidence"],
        )
        # OK 만 캐시한다. 캐시에서 꺼낸 답변은 OK 로 나가므로, LOW_CONFIDENCE 를 넣으면
        # 다음 같은 질문에서 "근거 부족" 표시가 사라진다
        if state["status"] == AnswerStatus.OK:
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

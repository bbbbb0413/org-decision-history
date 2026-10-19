"""검색 파이프라인 평가. 실제 인덱스에 질문을 던져 하이브리드 검색 → 재순위화 → 근거 판정까지 운영 코드 그대로 돌린다.

질의 확장(HyDE·분해)은 기본으로 끄고 원래 질문으로 검색한다. --expand 를 주면 운영과 같은 질의 확장 노드를 거친다
(질문마다 경량 LLM 을 더 부르므로 한도를 그만큼 더 쓴다). 질의 확장 방식을 바꾸는 변경의 전후 비교에 쓴다. 임베딩 API·reranker-service 와, 근거 충분성 판정이 켜져 있으면
(EVIDENCE_JUDGE_ENABLED) 벡터 유사도 거름을 통과한 질문마다 경량 LLM 을 한 번 부른다. Groq 무료 한도(경량 모델 분당 8,000토큰)에
맞춰 판정 사이를 --judge-pause 초 쉰다 — 한도를 넘기면 판정이 실패해 1차 거름으로 대신 정해져 결과가 달라진다.
결과는 그 환경의 색인(문서·청크 수가 보고서에 남는다)에 묶인다. 색인에 정답 문서가 없는 질문은 건너뛴다.

    docker compose exec rag-service python -m evaluation.retrieval_eval [--out 보고서.json]

설정은 운영과 같은 환경변수(EVIDENCE_GATE_* 등)를 읽는다. 근거 판정 기준을 바꿔 보려면 --gate-sweep 으로
리랭커 점수·낱말 겹침 기준 조합별 통과율을 한 번에 본다(검색은 한 번만 한다).
"""
import argparse
import asyncio
import dataclasses
import json
import sys
import time

from app.auth_client import PermissionScope
from app.dependencies import Dependencies
from app.search.models import SearchHit
from app.search.query_planner import QueryPlan
from app.search.relevance_gate import EvidenceGate, has_evidence
from evaluation.dataset import (
    Question, breakdown, covers_all_groups, first_gold_rank, latency_summary, load_questions, ranking_summary, select,
)

# 관리자 범위 — 권한 필터가 아니라 검색 품질을 잰다
ADMIN_SCOPE = PermissionScope(system_admin=True)
DEFAULT_SWEEP = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 0.5)
# 낱말 겹침 기준. 1.01 은 겹침 판정을 끈 것과 같다(비율은 1 을 넘지 못한다)
DEFAULT_COVERAGE_SWEEP = (0.34, 0.5, 0.67, 1.01)


async def _chunk_keys(deps: Dependencies) -> tuple[dict[int, str], int, int]:
    """문서 id → 제목, 그리고 색인 규모(문서 수, 청크 수)."""
    async with deps.pool.acquire() as conn:
        titles = {r["id"]: r["title"] for r in await conn.fetch(
            "SELECT d.id, d.title FROM document.document d WHERE d.id IN (SELECT DISTINCT document_id FROM indexing.document_chunk)"
        )}
        chunks = await conn.fetchval("SELECT count(*) FROM indexing.document_chunk")
    return titles, len(titles), int(chunks)


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _key(hit: SearchHit, titles: dict[int, str]) -> str:
    return hit.section_path or titles.get(hit.document_id, f"#{hit.document_id}")


def _gate_path(question: str, hits: list[SearchHit], gate: EvidenceGate) -> str | None:
    """근거 판정을 어느 갈래로 통과했는지. 통과 못 하면 None."""
    if any(h.rerank_score is not None and h.rerank_score >= gate.rerank_score for h in hits):
        return "rerank"
    if any(h.score is not None and h.score >= gate.vector_score for h in hits):
        return "vector"
    return "coverage" if has_evidence(question, hits, gate) else None


def _summarize(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    out: dict = {"n": len(rows)}
    if answerable:
        # 1차 검색 순위 그대로 상위 근거를 썼다면 / 재순위화 후 실제 근거
        out["evidence_without_rerank"] = ranking_summary([r["candidate_rank"] if r["candidate_rank"] and r["candidate_rank"] <= r["evidence_count"] else None for r in answerable])
        out["evidence"] = ranking_summary([r["evidence_rank"] for r in answerable])
        out["answerable_pass"] = round(sum(r["passed"] for r in answerable) / len(answerable), 3)
        out["answerable_outcomes"] = _count(r["verdict"] for r in answerable)
    if unanswerable:
        out["unanswerable_reject"] = round(sum(not r["passed"] for r in unanswerable) / len(unanswerable), 3)
        out["unanswerable_outcomes"] = _count(r["verdict"] for r in unanswerable)
    if any("complexity" in r for r in rows):
        # --expand 일 때 질의 확장이 고른 갈래와 검색 질의 수
        out["routes"] = _count(r["complexity"] for r in rows if "complexity" in r)
        out["multi_query"] = round(sum(len(r.get("queries", [])) > 1 for r in rows) / len(rows), 3)
    # 상충 질문(양쪽 주장)·multi 질문(두 대상): 실제 근거(재순위화 후)에 모든 정답 묶음이 하나 이상씩 들었는가
    for kind in ("conflict", "multi"):
        grouped = [r for r in rows if r["kind"] == kind and "covers_all" in r]
        if grouped:
            out[f"{kind}_covers_all"] = round(sum(r["covers_all"] for r in grouped) / len(grouped), 3)
    return out


async def evaluate(deps: Dependencies, sweep: tuple[float, ...], coverage_sweep: tuple[float, ...] = DEFAULT_COVERAGE_SWEEP,
                   split: str = "all", judge_pause: float = 20.0, expand: bool = False,
                   kinds: tuple[str, ...] = ()) -> dict:
    questions = [q for q in select(load_questions(), split) if not kinds or q.kind in kinds]
    titles, doc_count, chunk_count = await _chunk_keys(deps)
    indexed = set(titles.values())
    nodes, cfg = deps.nodes, deps.settings
    gate = cfg.evidence_gate()

    rows, skipped = [], []
    expand_s, retrieve_s, rerank_s, judge_s = [], [], [], []
    kept: list[tuple[Question, list[SearchHit]]] = []
    for q in questions:
        # 정답 문서가 색인에 없으면(수동 등록 데모 문서 등) 잴 수 없다. 상충 질문은 양쪽 문서가 모두 있어야 한다
        groups = q.gold_groups or ((q.gold,) if q.answerable else ())
        if any(not any(k.split(" > ")[0] in indexed for k in g) for g in groups):
            skipped.append(q.id)
            continue
        state = {"rewritten_question": q.question, "search_queries": [q.question], "scope": ADMIN_SCOPE}
        expansion = None
        if expand:
            # 운영 plan_query 와 같은 계획(대화 맥락 없음 = 첫 질문). 계획을 끈 설정이면 원래 질문 하나다
            te = time.perf_counter()
            plan = await deps.query_planner.plan(q.question) if deps.query_planner else QueryPlan(q.question)
            expansion = await nodes.search_queries_for(plan)
            expand_s.append(time.perf_counter() - te)
            state = {**state, **expansion}
        t0 = time.perf_counter()
        candidates = (await nodes.retrieve(state))["hits"]
        t1 = time.perf_counter()
        evidence = (await nodes.rerank({**state, "hits": candidates}))["hits"]
        t2 = time.perf_counter()
        retrieve_s.append(t1 - t0)
        rerank_s.append(t2 - t1)
        kept.append((q, evidence))
        # 운영과 같은 최종 판정(1차 거름 → 리랭커 확신 → 경량 LLM). LLM 분당 한도를 넘지 않게 판정한 뒤엔 잠시 쉰다
        t3 = time.perf_counter()
        verdict = await nodes.judge_evidence({**state, "hits": evidence})
        judge_s.append(time.perf_counter() - t3)
        if verdict["evidence_outcome"].startswith(("judged", "unjudged")):
            await asyncio.sleep(judge_pause)

        evidence_keys = [_key(h, titles) for h in evidence]
        row = {
            "id": q.id,
            "split": q.split,
            "kind": q.kind,
            "answerable": q.answerable,
            "gate": _gate_path(q.question, evidence, gate),
            "verdict": verdict["evidence_outcome"],
            "passed": verdict["evidence_verdict"] == "pass",
            "top_rerank": round(evidence[0].rerank_score, 6) if evidence and evidence[0].rerank_score is not None else None,
            "top_key": evidence_keys[0] if evidence_keys else None,
            "evidence_count": cfg.evidence_count,
        }
        if expansion is not None:
            row["complexity"] = expansion["complexity"]
            row["queries"] = expansion["search_queries"]
        if q.answerable:
            row["candidate_rank"] = first_gold_rank([_key(h, titles) for h in candidates], q.gold)
            row["evidence_rank"] = first_gold_rank(evidence_keys, q.gold)
        if q.gold_groups:
            row["covers_all"] = covers_all_groups(evidence_keys, q.gold_groups)
        rows.append(row)

    # 1차 거름 기준 스윕은 dev 로만 낸다 — 기준값은 dev 로 정하고 test 로 확인한다
    dev = {q.id: ev for q, ev in kept if q.split == "dev"}
    dev_q = {q.id: q for q, _ in kept if q.split == "dev"}

    def gate_rates(g: EvidenceGate) -> dict:
        passed = {i: _gate_path(dev_q[i].question, ev, g) is not None for i, ev in dev.items()}
        ans = [i for i in passed if dev_q[i].answerable]
        una = [i for i in passed if not dev_q[i].answerable]
        return {
            "answerable_pass": round(sum(passed[i] for i in ans) / max(len(ans), 1), 3),
            "unanswerable_reject": round(sum(not passed[i] for i in una) / max(len(una), 1), 3),
        }

    return {
        "kind": "retrieval",
        "corpus": {"documents": doc_count, "chunks": chunk_count},
        "dataset": {"split": split, "kinds": list(kinds), "questions": len(rows), "answerable": sum(r["answerable"] for r in rows),
                    "unanswerable": sum(not r["answerable"] for r in rows), "skipped": skipped},
        "settings": {
            "search_candidates": cfg.search_candidates,
            "evidence_count": cfg.evidence_count,
            "search_min_vector_similarity": cfg.search_min_vector_similarity,
            "evidence_gate": dataclasses.asdict(gate),
            "rerank_weight": cfg.rerank_weight,
            "rerank_saturated_weight": cfg.rerank_saturated_weight,
            "rerank_saturation_spread": cfg.rerank_saturation_spread,
            "reranker_enabled": deps.reranker.enabled,
            "judge_enabled": cfg.evidence_judge_enabled,
            "min_vector_similarity": cfg.evidence_min_vector_similarity,
            "query_rewriting": "expand" if expand else "off",
        },
        # 전체·dev·test·유형별: 근거 순위(재순위화 전후), 최종 판정 통과·거절률과 경로, 상충 양쪽 포함률
        "results": breakdown(rows, _summarize),
        # 1차 거름(낱말 겹침·점수)만 바꿨을 때의 dev 결과(경량 LLM 판정 제외). 키는 "rerank=…,coverage=…"
        "gate_sweep_dev": {
            f"rerank={t},coverage={c}": gate_rates(dataclasses.replace(gate, rerank_score=t, min_coverage=c))
            for c in coverage_sweep for t in sweep
        },
        "latency": {"expand": latency_summary(expand_s), "retrieve": latency_summary(retrieve_s), "rerank": latency_summary(rerank_s),
                    "judge": latency_summary(judge_s)},
        "questions": rows,
        # --dump-hits 일 때만 저장한다. 판정 방식을 바꿔 보는 실험을 검색 없이 하기 위한 원자료다
        "evidence_dump": [
            {"id": q.id, "split": q.split, "kind": q.kind, "question": q.question, "answerable": q.answerable, "gold": list(q.gold),
             "hits": [{"key": _key(h, titles), "rerank": h.rerank_score, "score": h.score,
                       "vector_similarity": h.vector_similarity, "content": h.content} for h in ev]}
            for q, ev in kept
        ],
    }


async def _run(args) -> dict:
    deps = await Dependencies().start()
    try:
        return await evaluate(deps, tuple(args.gate_sweep), tuple(args.coverage_sweep), args.split, args.judge_pause, args.expand,
                              tuple(args.kind or ()))
    finally:
        await deps.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="보고서를 JSON 으로 저장할 경로")
    parser.add_argument("--gate-sweep", type=float, nargs="*", default=list(DEFAULT_SWEEP))
    parser.add_argument("--coverage-sweep", type=float, nargs="*", default=list(DEFAULT_COVERAGE_SWEEP))
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--judge-pause", type=float, default=20.0, help="경량 LLM 판정 뒤 쉬는 초(분당 토큰 한도 보호)")
    parser.add_argument("--kind", nargs="*", help="이 유형의 질문만 잰다(예: multi). 한도를 아끼며 한 유형을 비교할 때")
    parser.add_argument("--expand", action="store_true", help="운영과 같은 질의 확장(재작성 제외)을 거쳐 검색한다")
    parser.add_argument("--dump-hits", help="질문별 근거 청크(본문 포함)를 이 경로에 JSON 으로 저장한다")
    args = parser.parse_args(argv)

    report = asyncio.run(_run(args))
    dump = report.pop("evidence_dump")
    if args.dump_hits:
        with open(args.dump_hits, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=1)
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "questions"}, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

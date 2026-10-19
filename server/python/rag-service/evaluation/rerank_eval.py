"""리랭커 모델 오프라인 평가. 고정 후보 문단(rerank_passages_ko.json) 전부를 질문마다 /rerank 로 매긴다.

운영(app/search/reranker.py HttpRerankClient)과 같이 문단을 한 건씩 보낸다. int8 모델은 여러 문단을 한 묶음으로 계산하면
점수가 함께 든 문단에 따라 바뀌어, 같은 평가를 다시 돌려도 결과가 달라진다.

인덱스·DB 가 필요 없어 어느 환경에서든 같은 결과가 나온다 — 모델을 바꿀 때 이 평가를 통과해야 한다(CI ci-model-eval.yml).
표준 라이브러리만 쓴다.

    cd server/python/rag-service
    RERANKER_API_KEY=... python -m evaluation.rerank_eval --url http://localhost:8090 --min-mrr 0.70

종료 코드: 기준 미달이면 1.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request

from evaluation.dataset import (
    breakdown, covers_all_groups, first_gold_rank, latency_summary, load_passages, load_questions, ranking_summary, select,
)


def _call(url: str, api_key: str, path: str, body: dict | None = None, timeout: float = 120.0):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url.rstrip("/") + path, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _summarize(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    out: dict = {"n": len(rows)}
    if answerable:
        out["ranking"] = ranking_summary([r["gold_rank"] for r in answerable])
        out["answerable_top_min"] = round(min(r["top_score"] for r in answerable), 6)
    if unanswerable:
        out["unanswerable_top_max"] = round(max(r["top_score"] for r in unanswerable), 6)
    # 상충 질문(양쪽 주장)·multi 질문(두 대상): 상위 5건에 모든 정답 묶음이 하나 이상씩 들었는가
    for kind in ("conflict", "multi"):
        grouped = [r for r in rows if r["kind"] == kind and "covers_all@5" in r]
        if grouped:
            out[f"{kind}_covers_all@5"] = round(sum(r["covers_all@5"] for r in grouped) / len(grouped), 3)
    return out


def evaluate(url: str, api_key: str = "", split: str = "all") -> dict:
    questions = select(load_questions(), split)
    passages = load_passages()
    keys = [p["key"] for p in passages]
    texts = [p["text"] for p in passages]

    info = _call(url, api_key, "/info")
    _call(url, api_key, "/rerank", {"query": "워밍업", "texts": texts[:4], "truncate": True})

    latencies, rows, gold_scores, other_scores = [], [], [], []
    for q in questions:
        started = time.perf_counter()
        scores = [float(_call(url, api_key, "/rerank", {"query": q.question, "texts": [t], "truncate": True})[0]["score"])
                  for t in texts]
        latencies.append(time.perf_counter() - started)
        order = sorted(range(len(texts)), key=lambda i: -scores[i])
        ranked = [keys[i] for i in order]
        row = {"id": q.id, "split": q.split, "kind": q.kind, "answerable": q.answerable,
               "top_score": round(scores[order[0]], 6), "top_key": ranked[0]}
        if q.answerable:
            row["gold_rank"] = first_gold_rank(ranked, q.gold)
            gold_scores.append(max(scores[i] for i, k in enumerate(keys) if k in q.gold))
            other_scores.extend(scores[i] for i, k in enumerate(keys) if k not in q.gold)
        if q.gold_groups:
            row["covers_all@5"] = covers_all_groups(ranked[:5], q.gold_groups)
        rows.append(row)

    return {
        "kind": "rerank",
        "model": info.get("served_model_name") or info.get("model_id"),
        "engine": f"tei {info.get('version')}" if "version" in info else None,
        "dataset": {"split": split, "questions": len(questions),
                    "answerable": sum(q.answerable for q in questions), "passages": len(passages)},
        # 전체·dev·test·유형별. 점수 기준(근거 판정 등)은 dev 로만 정한다
        "results": breakdown(rows, _summarize),
        "scores": {
            "gold_median": round(statistics.median(gold_scores), 4),
            "non_gold_median": round(statistics.median(other_scores), 6),
        },
        "latency_per_question": latency_summary(latencies),
        "questions": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("RERANKER_URL", "http://localhost:8090"))
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--min-mrr", type=float, default=None, help="이보다 낮으면 실패(종료 코드 1)")
    parser.add_argument("--out", help="보고서를 JSON 으로 저장할 경로")
    args = parser.parse_args(argv)

    # 컨테이너 안에서는 rag-service 와 같은 내부 키(INTERNAL_API_KEY)를 쓴다
    api_key = os.environ.get("RERANKER_API_KEY") or os.environ.get("INTERNAL_API_KEY", "")
    report = evaluate(args.url, api_key, args.split)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    summary = {k: v for k, v in report.items() if k != "questions"}
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    mrr = report["results"]["all"]["ranking"]["mrr"]
    if args.min_mrr is not None and mrr < args.min_mrr:
        print(f"실패: MRR {mrr} < 기준 {args.min_mrr}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

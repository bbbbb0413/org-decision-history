"""근거 충분성 판정기 평가. 고정 근거(judge_cases_ko.json)로 판정기만 돌린다 — 검색·스택이 필요 없다.

운영과 같은 단계를 밟는다: 벡터 유사도가 EVIDENCE_MIN_VECTOR_SIMILARITY 미만이면 LLM 없이 거절, 나머지는 판정기.
정답은 "답 있는 질문이고 정답 청크가 판정기에 보여 준 근거 안에 있다"다 — 검색이 정답을 놓친 질문은 "답 없음"이
맞는 판정이다. LLM(경량 모델)을 부르므로 Groq 한도를 쓴다: 판정 한 번 약 500토큰, dev 약 50건이면 약 25,000토큰.

    docker compose --profile app run --rm --no-deps -T --entrypoint python rag-service \\
      -m evaluation.judge_eval --split dev [--prompt evaluation/prompts/judge_v2_candidate.txt] [--out 보고서.json]

판정 캐시는 쓰지 않는다(프롬프트를 바꿔 가며 재야 하므로). 판정에 실패(한도 초과 등)한 건은 따로 센다 — 많으면 결과를 믿지 않는다.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.generation.factory import build_generator
from app.search.evidence_judge import EvidenceJudge
from app.search.models import SearchHit
from app.settings import settings
from evaluation.dataset import DATASETS, breakdown, load_passages, load_questions, select

CASES = DATASETS / "judge_cases_ko.json"


def _hits(case: dict, text: dict[str, str]) -> list[SearchHit]:
    return [
        SearchHit(0, 1, i, text[h["key"]], h["key"] if " > " in h["key"] else "", "", h["rerank"], h["vector_similarity"], h["rerank"])
        for i, h in enumerate(case["hits"])
    ]


def _summarize(rows: list[dict]) -> dict:
    judged = [r for r in rows if r["outcome"] != "unjudged"]
    out = {"n": len(rows), "unjudged": len(rows) - len(judged)}
    if judged:
        out["accuracy"] = round(sum(r["correct"] for r in judged) / len(judged), 3)
        should_pass = [r for r in judged if r["expected"]]
        should_reject = [r for r in judged if not r["expected"]]
        if should_pass:
            out["pass_rate"] = round(sum(r["passed"] for r in should_pass) / len(should_pass), 3)
        if should_reject:
            out["reject_rate"] = round(sum(not r["passed"] for r in should_reject) / len(should_reject), 3)
    return out


async def evaluate(split: str, prompt: str | None, pause: float, kinds: tuple[str, ...] = ()) -> dict:
    questions = {q.id: q for q in select(load_questions(), split) if not kinds or q.kind in kinds}
    text = {p["key"]: p["text"] for p in load_passages()}
    cases = [c for c in json.loads(CASES.read_text(encoding="utf-8"))["cases"] if c["id"] in questions]
    judge = EvidenceJudge(
        build_generator(settings),
        max_evidence=settings.evidence_judge_max_evidence,
        max_chars=settings.evidence_judge_max_chars,
        system_prompt=prompt,
    )
    rows = []
    for case in cases:
        q = questions[case["id"]]
        hits = _hits(case, text)
        shown = [h.key for h in hits[: settings.evidence_judge_max_evidence]]
        expected = q.answerable and any(h["key"] in q.gold for h in case["hits"][: settings.evidence_judge_max_evidence])
        similarities = [h.vector_similarity for h in hits if h.vector_similarity is not None]
        if similarities and max(similarities) < settings.evidence_min_vector_similarity:
            outcome, passed = "low_similarity", False
        else:
            judgement = await judge.judge(q.question, hits)
            if not judgement.evaluated:
                outcome, passed = "unjudged", False
            else:
                outcome, passed = ("judged_answerable", True) if judgement.answerable else ("judged_unanswerable", False)
            await asyncio.sleep(pause)
        rows.append({"id": q.id, "split": q.split, "kind": q.kind, "expected": expected, "passed": passed,
                     "outcome": outcome, "correct": passed == expected, "shown": shown})
        print(json.dumps(rows[-1], ensure_ascii=False), file=sys.stderr, flush=True)

    return {
        "kind": "judge",
        "model": settings.fallback_model,
        "prompt": "default" if prompt is None else "custom",
        "settings": {"max_evidence": settings.evidence_judge_max_evidence, "max_chars": settings.evidence_judge_max_chars,
                     "min_vector_similarity": settings.evidence_min_vector_similarity},
        "results": breakdown(rows, _summarize),
        "errors": [r for r in rows if not r["correct"] and r["outcome"] != "unjudged"],
        "questions": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("all", "dev", "test"), default="dev")
    parser.add_argument("--prompt", help="판정 시스템 프롬프트 파일. 없으면 운영 프롬프트")
    parser.add_argument("--pause", type=float, default=10.0, help="판정 뒤 쉬는 초(경량 모델 분당 토큰 한도 보호)")
    parser.add_argument("--kind", nargs="*", help="이 유형의 질문만 잰다(예: false_premise)")
    parser.add_argument("--out", help="보고서를 JSON 으로 저장할 경로")
    args = parser.parse_args(argv)

    prompt = Path(args.prompt).read_text(encoding="utf-8").strip() if args.prompt else None
    report = asyncio.run(evaluate(args.split, prompt, args.pause, tuple(args.kind or ())))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "questions"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

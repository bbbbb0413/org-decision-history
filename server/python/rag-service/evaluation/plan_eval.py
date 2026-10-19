"""질의 계획(QueryPlanner) 갈래 평가. 검색 없이 계획 호출만 질문마다 여러 번 돌려 갈래가 맞는지·흔들리지 않는지 본다.

- 두 대상을 묻는 질문(kind multi)은 complex 여야 하위 질의로 나눠 검색·재순위화한다
- 그 밖의 질문이 complex 로 가면 쓸데없이 나눠 검색한다(재순위화도 하위 질의 수만큼 든다)
- 같은 질문을 여러 번 물어 갈래가 한 번이라도 다르면 "흔들림"으로 센다

경량 LLM 을 부른다(호출 한 번 약 350토큰). 첫 질문(대화 맥락 없음)으로 부른다.

    docker compose --profile app run --rm --no-deps -T --entrypoint python rag-service \\
      -m evaluation.plan_eval --split dev --kind multi --repeat 3 [--out 보고서.json]
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from app.generation.factory import build_generator
from app.search.query_planner import QueryPlanner
from app.settings import settings
from evaluation.dataset import breakdown, load_questions, select


def _summarize(rows: list[dict]) -> dict:
    out: dict = {"n": len(rows)}
    if not rows:
        return out
    multi = [r for r in rows if r["kind"] == "multi"]
    others = [r for r in rows if r["kind"] != "multi"]
    if multi:
        # 반복 전체에서 complex 를 고른 비율
        out["multi_complex_rate"] = round(sum(r["kinds"].count("complex") for r in multi) / sum(len(r["kinds"]) for r in multi), 3)
    if others:
        out["other_complex_rate"] = round(sum(r["kinds"].count("complex") for r in others) / sum(len(r["kinds"]) for r in others), 3)
    out["unstable"] = round(sum(len(set(r["kinds"])) > 1 for r in rows) / len(rows), 3)
    out["kinds"] = dict(Counter(k for r in rows for k in r["kinds"]))
    return out


async def evaluate(split: str, kinds: tuple[str, ...], repeat: int, pause: float, prompt: str | None = None) -> dict:
    questions = [q for q in select(load_questions(), split) if not kinds or q.kind in kinds]
    planner = QueryPlanner(build_generator(settings), system_prompt=prompt)
    rows = []
    for q in questions:
        plans = []
        for _ in range(repeat):
            plans.append(await planner.plan(q.question))
            await asyncio.sleep(pause)
        rows.append({"id": q.id, "split": q.split, "kind": q.kind, "kinds": [p.kind for p in plans],
                     "sub_queries": [list(p.sub_queries) for p in plans]})
        print(json.dumps(rows[-1], ensure_ascii=False), file=sys.stderr, flush=True)
    return {
        "kind": "plan",
        "model": settings.fallback_model,
        "prompt": "default" if prompt is None else "custom",
        "settings": {"light_temperature": settings.light_temperature, "repeat": repeat},
        "results": breakdown(rows, _summarize),
        "questions": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("all", "dev", "test"), default="dev")
    parser.add_argument("--kind", nargs="*", help="이 유형의 질문만 잰다")
    parser.add_argument("--repeat", type=int, default=3, help="질문마다 계획을 몇 번 물을지")
    parser.add_argument("--pause", type=float, default=3.0, help="호출 뒤 쉬는 초(분당 토큰 한도 보호)")
    parser.add_argument("--prompt", help="계획 시스템 프롬프트 파일({max_sub_queries} 는 3으로 채운다). 없으면 운영 프롬프트")
    parser.add_argument("--out", help="보고서를 JSON 으로 저장할 경로")
    args = parser.parse_args(argv)
    prompt = Path(args.prompt).read_text(encoding="utf-8").strip().replace("{max_sub_queries}", "3") if args.prompt else None
    report = asyncio.run(evaluate(args.split, tuple(args.kind or ()), args.repeat, args.pause, prompt))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "questions"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""평가셋 읽기와 순위 지표. 표준 라이브러리만 쓴다 — CI 에서 rag-service 의존성 없이 리랭커 평가를 돌리기 위해서다.

정답은 문서 ID 가 아니라 청크 키(section_path, 없으면 문서 제목)로 적는다. 문서 ID 는 환경마다 다르다.

질문마다 split(dev/test)이 있다. 임계값·프롬프트는 dev 로만 정하고 test 는 결정한 뒤에 잰다 — 같은 질문으로 정하고
같은 질문으로 재면 처음 보는 질문에서의 성능을 알 수 없다. 원 질문과 그 변형(바꿔 말하기·오타)은 같은 group 이고
같은 split 에 둔다. 나뉘면 dev 에서 본 질문을 test 에서 말만 바꿔 다시 보는 셈이다.
"""
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

DATASETS = Path(__file__).parent / "datasets"
QUESTIONS = DATASETS / "questions_ko.jsonl"
PASSAGES = DATASETS / "rerank_passages_ko.json"


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answerable: bool
    # 답이 적힌 청크 키. 하나라도 걸리면 맞힌 것으로 본다
    gold: tuple[str, ...]
    group: str = ""
    split: str = "dev"
    # original / paraphrase(문서 낱말을 피한 바꿔 말하기) / typo / false_premise / conflict /
    # unanswerable_far / unanswerable_near(낱말·주제는 겹치지만 답 없음) / unanswerable_unknown(문서에 없는 사후 사실)
    # / multi(두 대상을 함께 묻는 질문 — 정답이 서로 다른 청크 두 묶음에 나뉜다, gold_groups)
    kind: str = "original"
    # 상충·multi 질문: 각 묶음에서 하나 이상이 근거에 들어야 양쪽(두 대상)을 다 본 것이다
    gold_groups: tuple[tuple[str, ...], ...] = ()


def load_questions(path: Path = QUESTIONS) -> list[Question]:
    questions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            q = json.loads(line)
            questions.append(Question(
                q["id"], q["question"], bool(q["answerable"]), tuple(q["gold"]),
                group=q.get("group", q["id"]), split=q.get("split", "dev"), kind=q.get("kind", "original"),
                gold_groups=tuple(tuple(g) for g in q.get("gold_groups", [])),
            ))
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("질문 id 가 겹친다")
    split_of_group: dict[str, str] = {}
    for q in questions:
        if q.answerable != bool(q.gold):
            raise ValueError(f"{q.id}: 답이 있는 질문만 정답 키를 가진다")
        if q.split not in ("dev", "test"):
            raise ValueError(f"{q.id}: split 은 dev 또는 test 다")
        if split_of_group.setdefault(q.group, q.split) != q.split:
            raise ValueError(f"{q.id}: 같은 group({q.group}) 이 dev 와 test 에 나뉘었다")
        if any(k not in q.gold for g in q.gold_groups for k in g):
            raise ValueError(f"{q.id}: gold_groups 의 키는 gold 에도 있어야 한다")
    return questions


def select(questions: list[Question], split: str) -> list[Question]:
    return questions if split == "all" else [q for q in questions if q.split == split]


def load_passages(path: Path = PASSAGES) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["passages"]


def first_gold_rank(ranked_keys: list[str], gold: tuple[str, ...]) -> int | None:
    """정답 키가 처음 나온 순위(1부터). 없으면 None."""
    for rank, key in enumerate(ranked_keys, start=1):
        if key in gold:
            return rank
    return None


def covers_all_groups(ranked_keys: list[str], groups: tuple[tuple[str, ...], ...]) -> bool:
    """상충 질문: 상위 근거가 모든 묶음(양쪽 주장)을 하나 이상씩 담는가."""
    return all(any(k in g for k in ranked_keys) for g in groups)


def breakdown(rows: list[dict], summarize) -> dict:
    """행 목록을 전체·split·유형별로 나눠 summarize 한다. 행은 split·kind 필드를 가진다."""
    out = {"all": summarize(rows)}
    for split in ("dev", "test"):
        part = [r for r in rows if r["split"] == split]
        if part:
            out[split] = summarize(part)
    out["by_kind"] = {k: summarize([r for r in rows if r["kind"] == k]) for k in sorted({r["kind"] for r in rows})}
    return out


def ranking_summary(ranks: list[int | None], ks: tuple[int, ...] = (1, 3, 5)) -> dict:
    """정답 순위 목록으로 MRR·hit@k 를 낸다. 순위가 없으면(못 찾음) 역순위 0 이다."""
    n = len(ranks)
    if n == 0:
        return {"n": 0}
    out = {"n": n, "mrr": round(sum(1 / r for r in ranks if r) / n, 3)}
    for k in ks:
        out[f"hit@{k}"] = round(sum(1 for r in ranks if r and r <= k) / n, 3)
    return out


def latency_summary(seconds: list[float]) -> dict:
    if not seconds:
        return {}
    ordered = sorted(seconds)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {"median_s": round(statistics.median(ordered), 2), "p95_s": round(p95, 2), "max_s": round(ordered[-1], 2)}

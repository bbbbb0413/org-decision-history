"""평가셋 파일의 정합성과 순위 지표 계산."""
import pytest

from evaluation.dataset import (
    breakdown, covers_all_groups, first_gold_rank, latency_summary, load_passages, load_questions, ranking_summary, select,
)


def test_every_gold_key_exists_in_frozen_passages():
    keys = [p["key"] for p in load_passages()]
    assert len(keys) == len(set(keys)), "후보 문단 키가 겹치면 정답 판정이 모호하다"
    missing = [(q.id, g) for q in load_questions() for g in q.gold if g not in keys]
    assert missing == []


def test_dataset_has_both_answerable_and_unanswerable_questions():
    questions = load_questions()
    assert any(q.answerable for q in questions)
    assert any(not q.answerable for q in questions)


def test_first_gold_rank_counts_from_one_and_takes_any_gold():
    assert first_gold_rank(["a", "b", "c"], ("c", "b")) == 2
    assert first_gold_rank(["a"], ("z",)) is None


def test_ranking_summary_treats_missing_as_zero_reciprocal_rank():
    out = ranking_summary([1, 2, None, 4], ks=(1, 3))
    assert out == {"n": 4, "mrr": round((1 + 0.5 + 0 + 0.25) / 4, 3), "hit@1": 0.25, "hit@3": 0.5}


def test_latency_summary():
    assert latency_summary([1.0, 3.0, 2.0]) == {"median_s": 2.0, "p95_s": 3.0, "max_s": 3.0}
    assert latency_summary([]) == {}


def test_question_ids_must_be_unique(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text('{"id":"a","question":"x","answerable":false,"gold":[]}\n' * 2, encoding="utf-8")
    with pytest.raises(ValueError):
        load_questions(p)


def test_each_split_has_answerable_and_unanswerable_questions():
    for split in ("dev", "test"):
        questions = select(load_questions(), split)
        assert any(q.answerable for q in questions) and any(not q.answerable for q in questions), split


def test_variants_of_a_question_stay_in_one_split():
    """원 질문과 바꿔 말하기가 dev·test 에 나뉘면 dev 에서 본 질문을 test 에서 다시 보는 셈이다."""
    split_of: dict[str, set[str]] = {}
    for q in load_questions():
        split_of.setdefault(q.group, set()).add(q.split)
    assert all(len(s) == 1 for s in split_of.values())


def test_paraphrases_avoid_document_wording():
    """바꿔 말하기는 정답 청크의 낱말을 피해야 키워드 검색에 기대지 않는 질문이 된다."""
    import statistics

    from app.search.relevance_gate import _appears, _terms_of

    text = {p["key"]: p["text"].lower() for p in load_passages()}
    overlaps = []
    for q in load_questions():
        if q.kind == "paraphrase":
            terms = _terms_of(q.question)
            gold = " ".join(text[k] for k in q.gold)
            overlaps.append(sum(_appears(t, gold) for t in terms) / len(terms))
    assert overlaps and statistics.median(overlaps) < 0.5


def test_covers_all_groups_needs_one_key_from_every_group():
    groups = (("a", "b"), ("c",))
    assert covers_all_groups(["x", "b", "c"], groups)
    assert not covers_all_groups(["a", "b"], groups)


def test_breakdown_splits_rows_by_split_and_kind():
    rows = [{"split": "dev", "kind": "original"}, {"split": "test", "kind": "original"}, {"split": "test", "kind": "typo"}]
    out = breakdown(rows, len)
    assert out == {"all": 3, "dev": 1, "test": 2, "by_kind": {"original": 2, "typo": 1}}


def test_group_split_mismatch_is_rejected(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text(
        '{"id":"a","group":"g","split":"dev","question":"x","answerable":false,"gold":[]}\n'
        '{"id":"b","group":"g","split":"test","question":"y","answerable":false,"gold":[]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_questions(p)

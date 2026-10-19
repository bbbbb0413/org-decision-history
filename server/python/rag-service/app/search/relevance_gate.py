"""검색 결과가 답변 근거로 쓸 만한지 판정한다.

전문검색은 낱말을 OR로 잇는다. 한국어는 조사가 붙어 AND로 묶으면 지나치게 자주
빈손이 되기 때문이다. 대신 낱말 하나만 겹쳐도 결과가 나오므로, 관계없는 질문에도
무언가가 걸린다. 그래서 건수가 아니라 겹침 정도로 판정한다.

이 방식은 어림짐작이다. 실제 임베딩 유사도 점수로 판정하는 편이 정확하다.
"""
import re
from dataclasses import dataclass

from app.search.models import SearchHit

MIN_TERM_LENGTH = 2


@dataclass(frozen=True)
class EvidenceGate:
    """근거 판정 기준. 운영 값은 설정(Settings.evidence_gate)에서 온다."""

    # 이 점수 이상인 청크가 하나라도 있으면 겹침을 보지 않고 통과
    rerank_score: float = 0.01
    vector_score: float = 0.65
    # 점수로 못 가리면 질문 낱말 중 이 비율 이상이 근거에 나타나야 통과
    min_coverage: float = 0.34


@dataclass(frozen=True)
class VectorFloors:
    """임베딩 모델로 잰 벡터 유사도 하한. 운영 값은 설정(Settings.vector_floors)에서 온다."""

    # 키워드 일치 없이 벡터로만 걸린 청크를 남기는 하한(검색)
    search: float
    # 근거의 벡터 유사도 최댓값이 이 미만이면 LLM 없이 거절(근거 판정)
    evidence: float

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def has_evidence(question: str, hits: list[SearchHit], gate: EvidenceGate = EvidenceGate()) -> bool:
    if not hits:
        return False

    # 1. Reranker 또는 벡터 점수가 신뢰 임계치를 넘는 고품질 근거가 있는 경우 즉시 통과
    for h in hits:
        if h.rerank_score is not None and h.rerank_score >= gate.rerank_score:
            return True
        # score 가 아니라 vector_similarity 를 본다. 재순위화가 score 를 리랭커 점수로 덮어써서, score 를 보면
        # 이 갈래가 리랭커 점수를 벡터 기준(0.65)과 비교하게 된다
        if h.vector_similarity is not None and h.vector_similarity >= gate.vector_score:
            return True

    # 2. 어휘 기반 커버리지 검사 (폴백)
    terms = _terms_of(question)
    if not terms:
        return False

    haystack = " ".join(h.content for h in hits if h.content).lower()
    matched = sum(1 for term in terms if _appears(term, haystack))
    return matched / len(terms) >= gate.min_coverage


def _appears(term: str, haystack: str) -> bool:
    """낱말이 근거에 나타나는지 본다.

    글자 그대로 비교하면 한국어에서 거의 걸리지 않는다. 질문의 "전환을"과 원문의
    "전환하기로"는 조사와 어미가 달라 다른 문자열이다. 뒤에서 최대 두 글자를 떼어
    가며 앞부분이 나타나는지 본다. 형태소 분석기 없이 조사·어미 대부분을 걷어내는
    어림수다.
    """
    for length in range(len(term), max(MIN_TERM_LENGTH, len(term) - 2) - 1, -1):
        if term[:length] in haystack:
            return True
    return False


def _terms_of(question: str) -> list[str]:
    cleaned = _NON_WORD.sub(" ", question)
    seen: dict[str, None] = {}
    for t in cleaned.split():
        lower = t.lower()
        if len(lower) >= MIN_TERM_LENGTH:
            seen.setdefault(lower, None)
    return list(seen.keys())

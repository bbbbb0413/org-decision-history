import re
from typing import Literal

KO_COMPLEX_KEYWORDS = [
    "비교해",
    "비교하",
    "차이",
    "차이점",
    "어떻게 다른",
    "어떤 점이 다른",
    "왜",
    "원인",
    "장단점",
]
EN_COMPLEX_PATTERN = re.compile(
    r"\b(compare|difference between|why|explain how|analyze|versus|vs\.|pros and cons)\b",
    re.IGNORECASE,
)
COMPOUND_CONJUNCTIONS = ["그리고", "또한", "아울러", "뿐만 아니라", "더불어"]

MIN_HYDE_WORDS = 3
MAX_HYDE_WORDS = 10
COMPLEX_WORD_THRESHOLD = 12


class QueryComplexityRouter:
    """질문 형태와 의도를 분석하여 검색 처리 전략을 결정한다.
    
    - complex: 복합 질문 (주제 분해 및 다중 검색 필요)
    - hyde: 단문/추상적 질문 (가상 문서를 생성하여 시맨틱 임베딩 보강)
    - simple: 일반 사실/키워드 질문 (단일 하이브리드 검색)
    """

    def route(self, question: str) -> Literal["simple", "complex", "hyde"]:
        trimmed = question.strip()
        words = [w for w in trimmed.split() if w]
        word_count = len(words)

        # 1. 다중/비교 분석 복합 질문 판정
        has_ko_complex = any(kw in trimmed for kw in KO_COMPLEX_KEYWORDS)
        has_en_complex = bool(EN_COMPLEX_PATTERN.search(trimmed))
        has_compound = any(c in trimmed for c in COMPOUND_CONJUNCTIONS)
        has_multi_q = trimmed.count("?") > 1

        if has_ko_complex or has_en_complex or has_compound or has_multi_q or word_count >= COMPLEX_WORD_THRESHOLD:
            return "complex"

        # 2. 키워드가 빈약한 단문 질의는 HyDE 가상 문서 보강
        if MIN_HYDE_WORDS <= word_count <= MAX_HYDE_WORDS:
            return "hyde"

        return "simple"


__all__ = ["QueryComplexityRouter"]

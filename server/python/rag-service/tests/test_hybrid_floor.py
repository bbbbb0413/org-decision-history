"""벡터로만 걸린 검색 결과를 남기는 기준."""
from unittest.mock import MagicMock

import pytest

from app.search.hybrid import HybridSearchService
from app.search.models import SearchHit


def vector_hit(similarity: float | None) -> SearchHit:
    return SearchHit(1, 1, 0, "본문", "문서 > 절", "", 0.0, similarity, None)


def service(floor: float | None) -> HybridSearchService:
    return HybridSearchService(MagicMock(), MagicMock(), MagicMock(), min_vector_similarity=floor)


@pytest.mark.parametrize(
    "floor, similarity, kept",
    [
        (0.5, 0.49, False),
        (0.5, 0.5, True),
        # 기준이 없는 대체 임베딩: 겹치는 n-gram 이 하나라도 있으면 남기고, 하나도 없으면(0) 버린다
        (None, 0.12, True),
        (None, 0.0, False),
        (None, None, False),
    ],
)
def test_vector_only_hits_respect_the_floor(floor, similarity, kept):
    hit = vector_hit(similarity)
    assert service(floor)._is_relevant(hit, keyword_matched=set()) is kept


def test_keyword_matches_are_kept_regardless_of_similarity():
    hit = vector_hit(0.0)
    assert service(None)._is_relevant(hit, keyword_matched={hit.key}) is True

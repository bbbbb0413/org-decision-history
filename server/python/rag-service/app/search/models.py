"""검색 결과 한 건."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    document_id: int
    document_version: int
    chunk_no: int
    content: str | None
    section_path: str | None
    chunk_tag: str | None
    score: float
    # 벡터 검색에서 나온 실제 코사인 유사도(0~1). 전문검색으로만 걸린 청크는 None —
    # 두 검색은 점수 단위가 달라 하나로 합칠 수 없다.
    vector_similarity: float | None
    rerank_score: float | None = None

    @property
    def key(self) -> str:
        """같은 청크인지 판정한다. 두 검색 결과를 병합할 때 쓴다."""
        return f"{self.document_id}:{self.document_version}:{self.chunk_no}"

    def with_rerank(self, rerank_score: float) -> "SearchHit":
        """2차 재순위화(Reranker) 점수를 반영한 새 SearchHit 객체를 반환한다."""
        return SearchHit(
            document_id=self.document_id,
            document_version=self.document_version,
            chunk_no=self.chunk_no,
            content=self.content,
            section_path=self.section_path,
            chunk_tag=self.chunk_tag,
            score=rerank_score,
            vector_similarity=self.vector_similarity,
            rerank_score=rerank_score,
        )

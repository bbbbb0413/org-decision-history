"""Qdrant 벡터 검색. LangChain BaseRetriever 어댑터를 겸한다.

권한 필터를 질의에 넣는다 — 결과를 받은 뒤에 거르면 상위 N건이 모두 권한 밖
문서일 때 아무것도 안 남는다. 처음부터 범위 안에서만 찾아야 한다.

Qdrant 접근과 필터 조립은 history_shared.vector 를 쓴다(색인 쪽과 같은 규약).
여기서는 PermissionScope 를 필터로 바꾸는 일과 LangChain BaseRetriever 어댑터만 맡는다 —
EnsembleRetriever(hybrid.py)에 그대로 꽂아 쓰기 위해서다.
"""
from typing import Any

import structlog
from history_shared.embeddings import Embedder
from history_shared.vector import ChunkVectorStore, scope_filter
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict
from qdrant_client import models

from app.auth_client import PermissionScope
from app.settings import Settings

log = structlog.get_logger()


class VectorSearchClient:
    """Qdrant에 직접 검색을 보내는 저수준 클라이언트. 임베딩은 모른다."""

    def __init__(self, settings: Settings, store: ChunkVectorStore | None = None):
        self._store = store or ChunkVectorStore(settings)

    async def close(self) -> None:
        await self._store.close()

    async def search(self, query_vector: list[float], scope: PermissionScope, limit: int) -> list[models.ScoredPoint]:
        if scope.is_empty():
            return []
        try:
            # 지금 규모(문서 수백 건)에서는 완전 탐색 비용이 미미하므로 정확도를 택한다(store.search 기본값).
            # 필터와 결합된 근사(HNSW) 검색은 후보를 통째로 놓칠 수 있다.
            return await self._store.search(query_vector, query_filter=self._scope_filter(scope), limit=limit)
        except Exception as e:  # noqa: BLE001 — 벡터 검색 실패는 키워드 검색만으로 진행
            log.warning("벡터 검색 실패. 키워드 검색만으로 진행한다", error=str(e))
            return []

    @staticmethod
    def _scope_filter(scope: PermissionScope) -> models.Filter | None:
        """시스템 관리자는 전체를 보므로 필터를 걸지 않는다. 차단 정책은 허용보다 우선한다."""
        return scope_filter(
            scope.to_scope_keys(),
            denied_document_ids=scope.denied_document_ids,
            system_admin=scope.system_admin,
        )


def chunk_key(document_id: int, document_version: int, chunk_no: int) -> str:
    """같은 청크인지 판정하는 키. EnsembleRetriever의 id_key로 쓴다(SearchHit.key() 와 같은 규칙)."""
    return f"{document_id}:{document_version}:{chunk_no}"


class VectorRetriever(BaseRetriever):
    """EnsembleRetriever에 꽂는 벡터 검색 어댑터. 요청(scope)마다 새로 만든다."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: VectorSearchClient
    embedder: Embedder
    scope: PermissionScope
    limit: int = 20

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        # BaseRetriever의 추상 메서드라 자리를 채워야 한다. 이 서비스는 asyncio
        # 전용(aiokafka)이라 동기 경로는 실제로 호출되지 않는다.
        raise NotImplementedError("VectorRetriever는 비동기 전용이다 — ainvoke()를 쓴다.")

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun, **kwargs: Any
    ) -> list[Document]:
        try:
            vector = await self.embedder.embed_query(query)
        except Exception as e:  # noqa: BLE001 — 임베딩 실패 시 키워드 검색만으로 진행 (HybridSearchService와 동일)
            log.warning("질문 임베딩 실패. 키워드 검색만으로 진행한다", error=str(e))
            return []
        points = await self.client.search(vector, self.scope, self.limit)
        docs = []
        for point in points:
            payload = point.payload or {}
            if not payload:
                continue
            similarity = float(point.score)
            key = chunk_key(payload["document_id"], payload["document_version"], payload["chunk_no"])
            docs.append(
                Document(
                    # 본문은 키워드 색인에서 채운다. 벡터 저장소에 본문을 중복 보관하지 않는다.
                    page_content="",
                    metadata={
                        "chunk_key": key,
                        "document_id": int(payload["document_id"]),
                        "document_version": int(payload["document_version"]),
                        "chunk_no": int(payload["chunk_no"]),
                        "section_path": None,
                        "chunk_tag": payload.get("chunk_tag"),
                        "vector_similarity": similarity,
                    },
                )
            )
        return docs

"""Qdrant document_chunks 컬렉션 접근.

규약: contracts/qdrant-chunk-payload.schema.json, contracts/indexing/README.md §8·§9·§11.
"""
import hashlib
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import structlog
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from history_shared.config import QdrantSettings
from history_shared.embeddings import DIMENSION

log = structlog.get_logger()


def point_id(document_id: int, version: int, chunk_no: int) -> str:
    """(문서, 판, 청크 번호)로 정해지는 ID. 재색인하면 같은 point 를 덮어써 중복이 생기지 않는다."""
    digest = hashlib.sha256(f"{document_id}:{version}:{chunk_no}".encode()).digest()
    return str(uuid.UUID(bytes=hashlib.md5(digest).digest(), version=3))


def scope_key(organization_id: int, team_id: int | None) -> str:
    return f"team:{team_id}" if team_id is not None else f"org:{organization_id}"


def scope_filter(
    scope_keys: Iterable[str],
    *,
    denied_document_ids: Iterable[int] = (),
    system_admin: bool = False,
) -> models.Filter | None:
    """권한 필터. 결과를 받은 뒤 거르면 상위 N건이 모두 권한 밖일 때 아무것도 남지 않으므로 질의에 넣는다.

    시스템 관리자는 거르지 않는다(None). 차단 문서는 허용보다 우선한다.
    허용 키가 비었는데 필터를 만들면 조건 없는 검색이 되므로 거부한다. 호출자가 먼저 빈 권한을 걸러야 한다.
    """
    if system_admin:
        return None
    keys = list(scope_keys)
    if not keys:
        raise ValueError("scope_keys 가 비어 있다. 권한이 없는 요청은 검색하지 않는다")
    denied = [
        models.FieldCondition(key="document_id", match=models.MatchValue(value=doc_id))
        for doc_id in denied_document_ids
    ]
    return models.Filter(
        should=[models.FieldCondition(key="scope_key", match=models.MatchValue(value=key)) for key in keys],
        must_not=denied or None,
    )


def chunk_payload(
    *,
    document_id: int,
    document_version: int,
    chunk_no: int,
    scope_key: str,
    chunk_tag: str,
    document_type: str,
    authored_at: str | None,
    model_name: str,
    organization_id: int,
    dim: int = DIMENSION,
) -> dict:
    """qdrant-chunk-payload.schema.json 모양의 payload.

    organization_id: 선택 필드다(스키마상 additive).
    scope_key는 team이 있으면 team:{id}로 조직을 가리지 않아, 거버넌스가 조직 전체를
    비교 대상으로 검색하려면 이 필드가 따로 필요하다.
    """
    return {
        "document_id": document_id,
        "document_version": document_version,
        "chunk_no": chunk_no,
        "scope_key": scope_key,
        "chunk_tag": chunk_tag,
        "document_type": document_type,
        "authored_at": authored_at,
        "model_name": model_name,
        "organization_id": organization_id,
        "dim": dim,
    }


@dataclass(frozen=True)
class ChunkPoint:
    id: str
    vector: Sequence[float]
    payload: dict


class ChunkVectorStore:
    def __init__(
        self,
        settings: QdrantSettings,
        *,
        client: AsyncQdrantClient | None = None,
        dimension: int = DIMENSION,
    ):
        self._client = client or AsyncQdrantClient(url=settings.qdrant_url)
        self._collection = settings.qdrant_collection
        self._dimension = dimension

    @property
    def collection(self) -> str:
        return self._collection

    async def close(self) -> None:
        await self._client.close()

    async def ensure_collection(self) -> bool:
        """컬렉션이 없을 때만 만든다. 조회 자체가 실패하면 그대로 올린다. 만들었으면 True."""
        if await self._client.collection_exists(self._collection):
            return False
        try:
            await self._client.create_collection(
                self._collection,
                vectors_config=models.VectorParams(size=self._dimension, distance=models.Distance.COSINE),
            )
        except UnexpectedResponse as e:
            if e.status_code == 409:  # 다른 워커가 먼저 만들었다
                return False
            raise
        log.info("Qdrant 컬렉션을 만들었다", collection=self._collection, dimension=self._dimension)
        return True

    async def delete_document(self, document_id: int) -> None:
        """문서의 모든 버전 point 를 지운다. 재색인 전과 삭제 반영 때 부른다."""
        await self._client.delete(
            self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                )
            ),
            wait=True,
        )

    async def upsert(self, points: Sequence[ChunkPoint]) -> None:
        if not points:
            return
        await self._client.upsert(
            self._collection,
            points=[models.PointStruct(id=p.id, vector=list(p.vector), payload=p.payload) for p in points],
            wait=True,
        )

    async def search(
        self,
        vector: Sequence[float],
        *,
        query_filter: models.Filter | None,
        limit: int,
        exact: bool = True,
    ) -> list[models.ScoredPoint]:
        """exact=True 는 완전 탐색이다. 필터와 결합한 근사 검색은 후보를 통째로 놓칠 수 있다."""
        result = await self._client.query_points(
            collection_name=self._collection,
            query=list(vector),
            limit=limit,
            with_payload=True,
            search_params=models.SearchParams(exact=exact),
            query_filter=query_filter,
        )
        return result.points

    async def count(self) -> int:
        return (await self._client.count(self._collection, exact=True)).count

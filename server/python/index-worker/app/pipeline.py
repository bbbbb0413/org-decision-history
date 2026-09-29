"""문서 하나를 색인한다.

본문 추출 → 분할 → 임베딩 → 벡터 저장 → 전문검색 색인 순서로 돈다.
실패는 단계명과 재시도 가능 여부를 함께 던진다. 규약: contracts/indexing/README.md §1.
"""
import asyncio
from enum import StrEnum
from typing import Protocol

import structlog
from history_shared.embeddings import DIMENSION, Embedder, EmbeddingError
from history_shared.events import DocumentIndexedEvent
from history_shared.vector import ChunkPoint, ChunkVectorStore, chunk_payload, point_id

from app.chunk import Chunk, ChunkHeader, chunk_document
from app.chunks_store import ChunkStore
from app.documents import DocumentReader, DocumentSource
from app.parse import ParsedDocument, ParseError, parse_document

log = structlog.get_logger()


class IndexStage(StrEnum):
    PARSE = "PARSE"
    CHUNK = "CHUNK"
    EMBED = "EMBED"
    VECTOR_UPSERT = "VECTOR_UPSERT"
    KEYWORD_INDEX = "KEYWORD_INDEX"
    # 색인 완료를 governance-worker에 알린다. migrations/V2 참고
    EVENT_PUBLISH = "EVENT_PUBLISH"


class IndexEventPublisher(Protocol):
    """stream:index-events 발행. 실패하면 파이프라인이 재시도 대상으로 돌린다(재색인은 멱등이라 안전하다)."""

    async def publish(self, event: DocumentIndexedEvent) -> None: ...


class IndexingError(Exception):
    """어느 단계에서 왜 실패했는지, 다시 시도할 만한지. 관리자 화면이 개입할 항목과 기다릴 항목을 나누는 기준이다."""

    def __init__(self, stage: IndexStage, message: str, *, retryable: bool):
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable


class FileSource(Protocol):
    async def read(self, storage_key: str) -> bytes: ...


class IndexingPipeline:
    def __init__(
        self,
        documents: DocumentReader,
        files: FileSource,
        embedder: Embedder,
        vectors: ChunkVectorStore,
        chunks: ChunkStore,
        events: IndexEventPublisher | None = None,
    ):
        self._documents = documents
        self._files = files
        self._embedder = embedder
        self._vectors = vectors
        self._chunks = chunks
        self._events = events

    async def index(self, document_id: int) -> int:
        """저장한 청크 수를 돌려준다. 삭제 반영이면 0."""
        source = await self._documents.find_by_id(document_id)
        if source is None:
            raise IndexingError(IndexStage.PARSE, "문서를 찾을 수 없습니다", retryable=False)

        # 삭제 여부는 이벤트 종류가 아니라 읽은 시점의 문서 상태로 판단한다
        if source.deleted:
            return await self.remove_from_index(document_id)

        parsed = await self._parse(source)
        chunks = self._split(source, parsed)
        vectors = await self._embed(chunks)
        point_ids = [point_id(source.id, source.version, chunk.chunk_no) for chunk in chunks]

        await self._store_vectors(source, chunks, vectors, point_ids)
        await self._store_keyword_index(source, chunks, point_ids)
        await self._publish_indexed(source, len(chunks))
        log.info("색인 완료", documentId=source.id, version=source.version, chunks=len(chunks))
        return len(chunks)

    async def remove_from_index(self, document_id: int) -> int:
        try:
            await self._vectors.delete_document(document_id)
        except Exception as e:  # noqa: BLE001
            raise IndexingError(IndexStage.VECTOR_UPSERT, f"벡터 색인 제거 실패: {e}", retryable=True) from e
        try:
            removed = await self._chunks.remove_all_of(document_id)
        except Exception as e:  # noqa: BLE001
            raise IndexingError(IndexStage.KEYWORD_INDEX, f"전문검색 색인 제거 실패: {e}", retryable=True) from e
        log.info("색인 제거", documentId=document_id, chunks=removed)
        return 0

    async def _parse(self, source: DocumentSource) -> ParsedDocument:
        try:
            # 원본 파일이 있으면 그쪽에서 뽑는다. 헤딩 구조가 본문 필드보다 풍부하다
            if source.storage_key is not None:
                data = await self._files.read(source.storage_key)
                return await asyncio.to_thread(parse_document, data, source.original_filename)
            # 원본이 없으면 화면에서 입력한 본문을 마크다운으로 본다
            return await asyncio.to_thread(parse_document, source.content.encode("utf-8"), "content.md")
        except ParseError as e:
            raise IndexingError(IndexStage.PARSE, str(e), retryable=e.retryable) from e
        except Exception as e:  # noqa: BLE001 — 객체 저장소 읽기 실패 등
            raise IndexingError(IndexStage.PARSE, f"원본을 읽을 수 없습니다: {e}", retryable=True) from e

    def _split(self, source: DocumentSource, parsed: ParsedDocument) -> list[Chunk]:
        try:
            chunks = chunk_document(parsed, ChunkHeader(source.title, source.authored_at, source.project_name))
        except Exception as e:  # noqa: BLE001
            raise IndexingError(IndexStage.CHUNK, f"문서 분할 실패: {e}", retryable=True) from e
        if not chunks:
            raise IndexingError(IndexStage.CHUNK, "본문이 비어 있어 색인할 내용이 없습니다", retryable=False)
        return chunks

    async def _embed(self, chunks: list[Chunk]) -> list[list[float]]:
        try:
            return await self._embedder.embed_documents([chunk.content for chunk in chunks])
        except EmbeddingError as e:
            raise IndexingError(IndexStage.EMBED, str(e), retryable=e.retryable) from e
        except Exception as e:  # noqa: BLE001
            raise IndexingError(IndexStage.EMBED, f"임베딩 실패: {e}", retryable=True) from e

    async def _store_vectors(
        self, source: DocumentSource, chunks: list[Chunk], vectors: list[list[float]], point_ids: list[str]
    ) -> None:
        try:
            # 이 문서의 기존 point 를 모두 걷어낸 뒤 현재 버전만 넣는다. 구버전 벡터가 남으면 옛 내용이 검색된다
            await self._vectors.delete_document(source.id)
            await self._vectors.upsert(
                [
                    ChunkPoint(
                        id=pid,
                        vector=vector,
                        payload=chunk_payload(
                            document_id=source.id,
                            document_version=source.version,
                            chunk_no=chunk.chunk_no,
                            scope_key=source.scope_key,
                            chunk_tag=chunk.tag,
                            document_type=source.document_type,
                            authored_at=source.authored_at.isoformat() if source.authored_at else None,
                            model_name=self._embedder.model_name,
                            organization_id=source.organization_id,
                            dim=DIMENSION,
                        ),
                    )
                    for chunk, vector, pid in zip(chunks, vectors, point_ids, strict=True)
                ]
            )
        except Exception as e:  # noqa: BLE001
            raise IndexingError(IndexStage.VECTOR_UPSERT, f"벡터 저장 실패: {e}", retryable=True) from e

    async def _store_keyword_index(self, source: DocumentSource, chunks: list[Chunk], point_ids: list[str]) -> None:
        try:
            await self._chunks.replace(source, chunks, point_ids)
        except Exception as e:  # noqa: BLE001
            raise IndexingError(IndexStage.KEYWORD_INDEX, f"전문검색 색인 실패: {e}", retryable=True) from e

    async def _publish_indexed(self, source: DocumentSource, chunk_count: int) -> None:
        """governance-worker가 이 이벤트로 상충 검사를 이어 붙인다. 발행기가 없으면(테스트 등) 건너뛴다."""
        if self._events is None:
            return
        event = DocumentIndexedEvent(
            document_id=source.id,
            version=source.version,
            chunk_count=chunk_count,
            organization_id=source.organization_id,
            team_id=source.team_id,
        )
        try:
            await self._events.publish(event)
        except Exception as e:  # noqa: BLE001 — 재시도하면 색인은 멱등이라 다시 발행해도 안전하다
            raise IndexingError(IndexStage.EVENT_PUBLISH, f"색인 완료 이벤트 발행 실패: {e}", retryable=True) from e

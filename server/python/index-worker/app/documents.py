"""색인 대상 문서를 읽는다.

document 스키마는 document-service 소유다. 여기서는 읽기만 한다. 색인은 대량 배치라 HTTP 왕복 대신 직접 읽는다.
"""
from dataclasses import dataclass
from datetime import date

import asyncpg
from history_shared.vector import scope_key

# 원본 파일은 "이 판 이하에서 가장 최근에 올라온 것"을 쓴다.
#
# 본문만 고쳐도 판이 올라가는데(`Document.update`) 파일은 올린 판에 묶여 있다. 정확한 판으로만
# 찾으면 오타 하나만 고쳐도 다음 색인이 원본을 못 찾고 본문(content.md)으로 되돌아가
# PDF 좌표가 조용히 사라진다.
# 원본이 실제로 바뀌면 그 판에 행이 생기므로 이 조회가 자동으로 새 파일을 고른다.
_SELECT = """
    SELECT d.id, d.current_version, d.title, d.content, d.authored_at, d.project_name,
           d.organization_id, d.team_id, d.document_type::text AS document_type,
           f.storage_key, f.original_filename,
           (d.deleted_at IS NOT NULL) AS deleted
    FROM document.document d
    LEFT JOIN LATERAL (
        SELECT df.storage_key, df.original_filename
        FROM document.document_file df
        WHERE df.document_id = d.id AND df.version <= d.current_version
        ORDER BY df.version DESC
        LIMIT 1
    ) f ON TRUE
"""


@dataclass(frozen=True)
class DocumentSource:
    id: int
    version: int
    title: str
    content: str
    authored_at: date | None
    project_name: str | None
    organization_id: int
    team_id: int | None
    document_type: str
    storage_key: str | None
    original_filename: str | None
    deleted: bool

    @property
    def scope_key(self) -> str:
        """색인 시점의 소속을 벡터 payload 와 청크에 새긴다. 검색 단계에서 이 값으로 거른다."""
        return scope_key(self.organization_id, self.team_id)


class DocumentReader:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def find_by_id(self, document_id: int) -> DocumentSource | None:
        row = await self._pool.fetchrow(_SELECT + " WHERE d.id = $1", document_id)
        return _to_source(row) if row else None

    async def find_all_active(self, organization_id: int | None = None, team_id: int | None = None) -> list[DocumentSource]:
        """일괄 색인 대상. 삭제된 문서는 뺀다."""
        rows = await self._pool.fetch(
            _SELECT
            + """
            WHERE d.deleted_at IS NULL
              AND ($1::bigint IS NULL OR d.organization_id = $1)
              AND ($2::bigint IS NULL OR d.team_id = $2)
            ORDER BY d.id
            """,
            organization_id,
            team_id,
        )
        return [_to_source(row) for row in rows]


def _to_source(row: asyncpg.Record) -> DocumentSource:
    return DocumentSource(
        id=row["id"],
        version=row["current_version"],
        title=row["title"],
        content=row["content"],
        authored_at=row["authored_at"],
        project_name=row["project_name"],
        organization_id=row["organization_id"],
        team_id=row["team_id"],
        document_type=row["document_type"],
        storage_key=row["storage_key"],
        original_filename=row["original_filename"],
        deleted=row["deleted"],
    )

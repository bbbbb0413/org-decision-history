"""출처에 문서 정보를 붙인다.

전송 직전에 원본 데이터베이스에서 문서 권한을 다시 확인한다. 검색 시점과 전송
시점 사이에 권한이 회수될 수 있고, 그 사이가 곧 유출 창이다. 권한이 사라진
출처는 안내 없이 목록에서 뺀다 — 안내하면 문서의 존재가 드러난다.
"""
import json

import asyncpg

from app.auth_client import PermissionScope
from app.search.models import SearchHit


class SourceResolver:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def resolve(self, hits: list[SearchHit], scope: PermissionScope) -> list[dict]:
        sources = []
        rank = 1
        for hit in hits:
            row = await self._pool.fetchrow(
                """
                SELECT d.title, d.document_type::text AS document_type, d.authored_at,
                       d.organization_id, d.team_id,
                       (SELECT f.id FROM document.document_file f
                         WHERE f.document_id = d.id AND f.version = d.current_version) AS file_id,
                       c.page_start, c.page_end, c.bboxes::text AS bboxes
                FROM document.document d
                LEFT JOIN indexing.document_chunk c
                       ON c.document_id = d.id AND c.document_version = $2 AND c.chunk_no = $3
                WHERE d.id = $1 AND d.deleted_at IS NULL
                """,
                hit.document_id,
                hit.document_version,
                hit.chunk_no,
            )
            if row is None or not self._visible(row, hit.document_id, scope):
                continue
            similarity = None
            if hit.vector_similarity is not None:
                similarity = round(min(0.9999, max(0.0, hit.vector_similarity)), 4)
            sources.append(
                {
                    "documentId": hit.document_id,
                    "documentTitle": row["title"],
                    "documentType": row["document_type"],
                    "authoredAt": row["authored_at"].isoformat() if row["authored_at"] else None,
                    "documentVersion": hit.document_version,
                    "chunkNo": hit.chunk_no,
                    "sectionPath": hit.section_path,
                    "chunkTag": hit.chunk_tag,
                    "similarity": similarity,
                    "rank": rank,
                    "fileId": row["file_id"],
                    # 바운딩 박스. PDF가 아니면 셋 다 None
                    "pageStart": row["page_start"],
                    "pageEnd": row["page_end"],
                    "bboxes": json.loads(row["bboxes"]) if row["bboxes"] else None,
                }
            )
            rank += 1
        return sources

    def _visible(self, row: asyncpg.Record, document_id: int, scope: PermissionScope) -> bool:
        """현재 권한 범위에 드는지 다시 판정한다."""
        if document_id in scope.denied_document_ids:
            return False
        if scope.system_admin:
            return True
        org_id = row["organization_id"]
        team_id = row["team_id"]
        return (
            document_id in scope.document_ids
            or (org_id is not None and org_id in scope.organization_ids)
            or (team_id is not None and team_id in scope.team_ids)
        )

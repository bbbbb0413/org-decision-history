"""Postgres 접근. asyncpg를 raw SQL로 직접 쓴다 (JdbcClient 스타일을 그대로 따른다).

모든 테이블명에 스키마를 명시적으로 붙인다(chat./indexing./document.) — JDBC의
currentSchema 검색 경로 같은 게 asyncpg에는 없으므로, 대신 항상 완전한 이름을 쓴다.
"""
from dataclasses import dataclass
from datetime import datetime

import asyncpg

from app.search.models import SearchHit


@dataclass(frozen=True)
class AnswerJob:
    job_key: str
    conversation_id: int
    user_id: int
    question: str
    status: str
    assistant_message_id: int | None


class Db:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # ---- answer_job ----

    async def find_job(self, job_key: str) -> AnswerJob | None:
        row = await self._pool.fetchrow(
            """
            SELECT job_key, conversation_id, user_id, question,
                   status::text AS status, assistant_message_id
            FROM chat.answer_job
            WHERE job_key = $1::uuid
            """,
            job_key,
        )
        if row is None:
            return None
        return AnswerJob(
            job_key=str(row["job_key"]),
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            question=row["question"],
            status=row["status"],
            assistant_message_id=row["assistant_message_id"],
        )

    async def mark_running(self, job_key: str) -> None:
        await self._set_status(job_key, "RUNNING", None)

    async def mark_completed(self, job_key: str, assistant_message_id: int) -> None:
        await self._set_status(job_key, "COMPLETED", assistant_message_id)

    async def mark_failed(self, job_key: str) -> None:
        await self._set_status(job_key, "FAILED", None)

    async def _set_status(self, job_key: str, status: str, assistant_message_id: int | None) -> None:
        await self._pool.execute(
            """
            UPDATE chat.answer_job
            SET status = $1::chat.job_status,
                assistant_message_id = COALESCE($2, assistant_message_id),
                updated_at = now()
            WHERE job_key = $3::uuid
            """,
            status,
            assistant_message_id,
            job_key,
        )

    # ---- conversation / message ----

    async def append_user_message(self, conversation_id: int, content: str, rewritten_query: str) -> int:
        message_id = await self._pool.fetchval(
            """
            INSERT INTO chat.message (conversation_id, role, content, rewritten_query)
            VALUES ($1, 'USER', $2, $3) RETURNING id
            """,
            conversation_id,
            content,
            rewritten_query,
        )
        await self._touch(conversation_id)
        return message_id

    async def append_assistant_message(
        self,
        conversation_id: int,
        content: str,
        model_name: str | None,
        status: str,
        latency_ms: int,
        sources: list[SearchHit],
    ) -> int:
        message_id = await self._pool.fetchval(
            """
            INSERT INTO chat.message (conversation_id, role, content, model_name, answer_status, latency_ms)
            VALUES ($1, 'ASSISTANT', $2, $3, $4::chat.answer_status, $5)
            RETURNING id
            """,
            conversation_id,
            content,
            model_name,
            status,
            latency_ms,
        )
        for rank, hit in enumerate(sources, start=1):
            similarity = None
            if hit.vector_similarity is not None:
                similarity = min(0.9999, max(0.0, hit.vector_similarity))
            await self._pool.execute(
                """
                INSERT INTO chat.answer_source
                    (message_id, document_id, document_version, chunk_no, similarity, rank)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                message_id,
                hit.document_id,
                hit.document_version,
                hit.chunk_no,
                similarity,
                rank,
            )
        await self._touch(conversation_id)
        return message_id

    async def rename_if_untitled(self, conversation_id: int, first_question: str) -> None:
        title = first_question if len(first_question) <= 60 else first_question[:60]
        await self._pool.execute(
            "UPDATE chat.conversation SET title = $1 WHERE id = $2 AND title = '새 대화'",
            title,
            conversation_id,
        )

    async def _touch(self, conversation_id: int) -> None:
        await self._pool.execute(
            "UPDATE chat.conversation SET updated_at = now() WHERE id = $1", conversation_id
        )

    async def recent_turns(self, conversation_id: int, limit: int) -> list[tuple[str, str, datetime]]:
        rows = await self._pool.fetch(
            """
            SELECT role::text AS role, content, created_at
            FROM chat.message
            WHERE conversation_id = $1
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            conversation_id,
            limit,
        )
        turns = [(r["role"], r["content"], r["created_at"]) for r in rows]
        return list(reversed(turns))

    async def turn_count(self, conversation_id: int) -> int:
        return await self._pool.fetchval(
            "SELECT count(*) FROM chat.message WHERE conversation_id = $1", conversation_id
        )

"""PostgreSQL 연결 풀.

asyncpg 에는 JDBC 의 currentSchema 같은 검색 경로 설정이 없다. 쿼리마다 스키마를 붙여 쓴다(document./indexing./chat.).
"""
import asyncpg

from history_shared.config import PostgresSettings


async def create_pool(
    settings: PostgresSettings,
    *,
    min_size: int = 2,
    max_size: int = 10,
    command_timeout: float | None = None,
) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
    )

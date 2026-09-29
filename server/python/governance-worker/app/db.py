"""PostgreSQL asyncpg 데이터베이스 클라이언트."""
import asyncpg
import structlog

log = structlog.get_logger()


class Db:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def find_document_with_content(self, document_id: int) -> dict | None:
        query = """
        SELECT d.id, d.title, d.organization_id, d.team_id, d.decision_status,
               d.authored_at, d.current_version, v.content
        FROM document.document d
        LEFT JOIN document.document_version v ON d.id = v.document_id
        WHERE d.id = $1 AND d.deleted_at IS NULL
        ORDER BY v.version DESC
        LIMIT 1
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, document_id)
            if not row:
                return None
            return dict(row)

    async def record_conflict(
        self,
        source_doc_id: int,
        target_doc_id: int,
        conflict_type: str,
        severity_score: float,
        source_clause: str,
        target_clause: str,
        reasoning: str,
        affected_teams: list[str],
        source_doc_version: int | None = None,
    ) -> int:
        """document.decision_conflicts 에 기록한다.

        컬럼은 reasoning(TEXT)·affected_teams(TEXT[])다.
        같은 (source_doc_id, target_doc_id, source_clause) 조합으로 다시 감지되면(재색인 등) 갱신한다.
        source_clause 는 길 수 있어 ON CONFLICT 대상은 원문 대신 md5 해시에 건다
        (server/spring/document-service V4__decision_conflicts_unique.sql 의 식 인덱스와 맞춘다).
        """
        query = """
        INSERT INTO document.decision_conflicts (
            source_doc_id, target_doc_id, conflict_type, severity_score,
            source_clause, target_clause, reasoning, affected_teams, status,
            source_doc_version
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, 'OPEN', $9
        )
        ON CONFLICT (source_doc_id, target_doc_id, (md5(source_clause)))
        DO UPDATE SET
            conflict_type = EXCLUDED.conflict_type,
            severity_score = EXCLUDED.severity_score,
            target_clause = EXCLUDED.target_clause,
            reasoning = EXCLUDED.reasoning,
            affected_teams = EXCLUDED.affected_teams,
            status = 'OPEN',
            source_doc_version = EXCLUDED.source_doc_version
        RETURNING id
        """
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                query,
                source_doc_id,
                target_doc_id,
                conflict_type,
                severity_score,
                source_clause,
                target_clause,
                reasoning,
                affected_teams,
                source_doc_version,
            )

    async def supersede_outdated_conflicts(self, source_doc_id: int, current_version: int) -> int:
        """개정 전 판을 보고 내려진 OPEN 상충을 닫는다.

        문서를 고치면 조항 원문이 바뀌어 재감지가 새 행을 만든다. 이전 판 기준 행을 닫지 않으면
        대시보드에 유령 상충이 쌓이므로, 재감지를 마친 뒤 이 메서드로 정리한다.

        - 사람이 이미 처리한 건(RESOLVED·DISMISSED)은 건드리지 않는다.
        - source_doc_version 이 NULL 인 행(이 컬럼이 생기기 전 기록)은 어느 판 기준인지
          알 수 없으므로 그대로 둔다 — 멀쩡한 상충을 임의로 닫는 것보다 남기는 쪽이 낫다.
        """
        query = """
        UPDATE document.decision_conflicts
        SET status = 'SUPERSEDED'
        WHERE source_doc_id = $1
          AND status = 'OPEN'
          AND source_doc_version IS NOT NULL
          AND source_doc_version < $2
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(query, source_doc_id, current_version)
            # asyncpg 는 "UPDATE <건수>" 형태로 돌려준다
            return int(result.split()[-1]) if result else 0

    async def find_team_channels(self, team_ids: list[int]) -> dict[int, dict]:
        """public.team_notification_channel 직접 조회.

        auth-service 가 이 테이블의 소유자이고 governance-worker 는 읽기만 한다.
        auth-service 는 document/indexing/chat 서비스와 달리 자기 스키마를 따로 두지 않고
        public 스키마를 그대로 쓴다(application.yml 에 spring.flyway.schemas 가 없다).
        행이 없는 team_id 는 결과 dict 에 아예 없다 — 그 팀은 "미설정"이다.
        """
        if not team_ids:
            return {}
        query = """
        SELECT team_id, slack_webhook_url, teams_webhook_url, notification_email
        FROM public.team_notification_channel
        WHERE team_id = ANY($1)
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, team_ids)
            return {row["team_id"]: dict(row) for row in rows}

    async def find_document_author_emails(self, document_ids: list[int]) -> list[str]:
        """상충에 걸린 문서의 작성자 이메일.

        그동안 알림은 팀 채널과 팀원 이메일로만 갔고 document.author_id 는 수신자 결정에
        쓰이지 않았다 — 내가 6개월 전에 내린 결정이 다른 팀 문서로 뒤집혀도 정작 나만
        몰랐다. 작성자는 그 결정을 근거로 계속 일하는 사람이라 가장 먼저 알아야 한다.

        author_id 가 NULL 인 문서(일괄 가져오기 등 계정과 연결되지 않은 것)는 건너뛴다.
        비활성 계정도 제외한다 — 퇴사자 메일함으로 보내지 않는다.
        """
        if not document_ids:
            return []
        query = """
        SELECT DISTINCT u.email
        FROM document.document d
        JOIN public.app_user u ON u.id = d.author_id
        WHERE d.id = ANY($1) AND d.author_id IS NOT NULL AND u.status = 'ACTIVE'
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, document_ids)
            return [row["email"] for row in rows]

    async def find_team_member_emails(self, team_id: int) -> list[str]:
        """팀 채널에 notification_email 이 없을 때 쓰는 개인 이메일 폴백."""
        query = """
        SELECT u.email
        FROM public.app_user u
        JOIN public.user_team ut ON ut.user_id = u.id
        WHERE ut.team_id = $1
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, team_id)
            return [row["email"] for row in rows]

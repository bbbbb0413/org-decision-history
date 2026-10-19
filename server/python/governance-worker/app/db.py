"""PostgreSQL asyncpg 데이터베이스 클라이언트."""
from dataclasses import dataclass

import asyncpg
import structlog

log = structlog.get_logger()

# 사람이 상충 처리 화면에서 닫은 상태. SUPERSEDED 는 워커가 닫는 값이라 여기 들지 않는다
HUMAN_CLOSED = frozenset({"RESOLVED", "DISMISSED"})


@dataclass(frozen=True)
class RecordedConflict:
    id: int
    created: bool
    status: str
    # 알림을 보낼지. 그 문서 쌍에서 같은 원문 텍스트로 처음 감지된 새 행만 참이다
    notify: bool
    # 같은 원문 텍스트에 대한 사람 판정을 이어받았으면 그 행 id
    inherited_from: int | None = None


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

    async def find_source_chunks(self, document_id: int) -> list[str]:
        """index-worker 가 색인한 이 문서의 청크 본문을 순서대로 돌려준다. 색인이 없으면 빈 목록.

        상충 검사의 원문을 검색 색인과 같은 텍스트로 맞추려고 쓴다. document_version.content 는 폼 본문이라
        원본 파일을 따로 올린 문서에서는 색인된 내용과 다르다. index-worker 는 문서마다 한 판의 청크만 남긴다.
        """
        rows = await self._pool.fetch(
            "SELECT content FROM indexing.document_chunk WHERE document_id = $1 ORDER BY chunk_no", document_id
        )
        return [r["content"] for r in rows]

    async def record_conflict(
        self,
        source_doc_id: int,
        target_doc_id: int,
        conflict_type: str,
        severity_score: float,
        severity_level: str,
        source_clause: str,
        target_clause: str,
        reasoning: str,
        affected_teams: list[str],
        source_doc_version: int | None = None,
        target_doc_version: int | None = None,
        source_text_hash: str | None = None,
        target_text_hash: str | None = None,
    ) -> "RecordedConflict":
        """document.decision_conflicts 에 기록하고, 알려야 하는지까지 정해 돌려준다.

        컬럼은 reasoning(TEXT)·affected_teams(TEXT[])다.
        같은 (source_doc_id, target_doc_id, source_clause) 조합으로 다시 감지되면(재색인 등) 갱신한다.
        source_clause 는 길 수 있어 ON CONFLICT 대상은 원문 대신 md5 해시에 건다
        (server/spring/document-service V4__decision_conflicts_unique.sql 의 식 인덱스와 맞춘다).

        사람의 판정을 지키는 규칙:
        - 같은 조항이 다시 감지되면: 사람이 닫은 RESOLVED·DISMISSED 는 상태를 그대로 둔다(판정 내용만 갱신).
          워커가 닫은 SUPERSEDED 는 다시 OPEN — 개정 뒤에도 같은 조항이 남아 있다는 뜻이다.
        - 조항 문구가 달라 새 행이 생기면: 같은 문서 쌍에서 **판정에 쓰인 텍스트가 같은** 행이 있는지 본다
          (조항을 뽑은 source 원문 창의 해시, 비교한 target 청크의 해시 — V8). 같은 텍스트를 다시 판정한 것이므로
          LLM 이 같은 내용을 다른 문구로 뽑았을 가능성이 크다.
          · 그 행들이 모두 사람이 닫은 것이면 새 행도 그 판정을 이어받아 닫힌 채로 기록한다(사람이 직접 내린 것 우선).
          · 하나라도 열려 있으면 새 행은 OPEN 으로 두되, 이 텍스트 쌍은 이미 알렸으므로 다시 알리지 않는다.
        - 알림은 그 텍스트 쌍에서 처음 감지된 행에만 보낸다. 판(version)이 아니라 텍스트로 보므로, 문서를 고쳐도
          고친 곳이 그 창·청크 밖이면 다시 알리지 않는다.
        해시를 모르는 옛 행(NULL)은 이어받기·중복 판단에 쓰지 않는다.
        """
        upsert = """
        INSERT INTO document.decision_conflicts (
            source_doc_id, target_doc_id, conflict_type, severity_score,
            source_clause, target_clause, reasoning, affected_teams, status,
            source_doc_version, severity_level, target_doc_version, source_text_hash, target_text_hash
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, 'OPEN', $9, $10, $11, $12, $13
        )
        ON CONFLICT (source_doc_id, target_doc_id, (md5(source_clause)))
        DO UPDATE SET
            conflict_type = EXCLUDED.conflict_type,
            severity_score = EXCLUDED.severity_score,
            severity_level = EXCLUDED.severity_level,
            target_clause = EXCLUDED.target_clause,
            reasoning = EXCLUDED.reasoning,
            affected_teams = EXCLUDED.affected_teams,
            status = CASE WHEN decision_conflicts.status IN ('RESOLVED', 'DISMISSED')
                          THEN decision_conflicts.status ELSE 'OPEN' END,
            source_doc_version = EXCLUDED.source_doc_version,
            target_doc_version = EXCLUDED.target_doc_version,
            source_text_hash = EXCLUDED.source_text_hash,
            target_text_hash = EXCLUDED.target_text_hash
        RETURNING id, (xmax = 0) AS created, status
        """
        same_texts = """
        SELECT id, status FROM document.decision_conflicts
        WHERE source_doc_id = $1 AND target_doc_id = $2
          AND source_text_hash = $3 AND target_text_hash = $4
        -- 이어받을 판정은 사람이 직접 내린 것을 먼저 고른다(워커가 이어받은 행을 다시 가리키지 않게)
        ORDER BY (COALESCE(resolved_by, '') = 'system'), resolved_at DESC NULLS LAST, id DESC
        """
        inherit = """
        UPDATE document.decision_conflicts
        SET status = $2, resolved_by = 'system', resolved_at = now(),
            resolution_note = format('같은 원문에 대한 이전 판정(#%s)을 이어받음', $3::bigint)
        WHERE id = $1
        """
        texts_known = source_text_hash is not None and target_text_hash is not None
        async with self._pool.acquire() as conn, conn.transaction():
            # 같은 쌍을 두 곳에서 동시에 기록하면 서로의 새 행을 못 보고 둘 다 알린다
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(format('decision_conflicts:%s:%s', $1::bigint, $2::bigint), 0))",
                source_doc_id,
                target_doc_id,
            )
            prior = await conn.fetch(same_texts, source_doc_id, target_doc_id, source_text_hash, target_text_hash) \
                if texts_known else []
            row = await conn.fetchrow(
                upsert,
                source_doc_id,
                target_doc_id,
                conflict_type,
                severity_score,
                source_clause,
                target_clause,
                reasoning,
                affected_teams,
                source_doc_version,
                severity_level,
                target_doc_version,
                source_text_hash,
                target_text_hash,
            )
            conflict_id, created, status = row["id"], row["created"], row["status"]
            if not created:
                return RecordedConflict(conflict_id, created=False, status=status, notify=False)
            if prior and all(p["status"] in HUMAN_CLOSED for p in prior):
                decided = prior[0]
                await conn.execute(inherit, conflict_id, decided["status"], decided["id"])
                return RecordedConflict(conflict_id, created=True, status=decided["status"], notify=False, inherited_from=decided["id"])
            return RecordedConflict(conflict_id, created=True, status=status, notify=not prior)

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

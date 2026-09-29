"""Db.record_conflict. 실제 PostgreSQL 로 돌린다(ON CONFLICT 대상이 md5 식 인덱스라 흉내 낼 수 없다).
"""
from app.db import Db


async def test_record_conflict_writes_reasoning_and_affected_teams(pool, add_document):
    source_id = await add_document(title="신규 정책")
    target_id = await add_document(title="기존 정책")
    db = Db(pool)

    conflict_id = await db.record_conflict(
        source_doc_id=source_id,
        target_doc_id=target_id,
        conflict_type="DIRECT_CONTRADICTION",
        severity_score=0.85,
        source_clause="원격 근무는 주 3회까지 허용한다.",
        target_clause="원격 근무는 금지한다.",
        reasoning="신규 조항이 기존 금지 규정과 정면으로 상충함",
        affected_teams=["인사팀", "보안팀"],
    )

    row = await pool.fetchrow("SELECT * FROM document.decision_conflicts WHERE id = $1", conflict_id)
    assert row["reasoning"] == "신규 조항이 기존 금지 규정과 정면으로 상충함"
    assert row["affected_teams"] == ["인사팀", "보안팀"]
    assert row["status"] == "OPEN"
    assert "description" not in row.keys()


async def test_record_conflict_is_idempotent_for_the_same_clause_pair(pool, add_document):
    source_id = await add_document()
    target_id = await add_document()
    db = Db(pool)
    kwargs = dict(
        source_doc_id=source_id,
        target_doc_id=target_id,
        conflict_type="DIRECT_CONTRADICTION",
        severity_score=0.7,
        source_clause="같은 조항",
        target_clause="기존 조항",
        reasoning="첫 감지",
        affected_teams=[],
    )

    first_id = await db.record_conflict(**kwargs)
    second_id = await db.record_conflict(**{**kwargs, "severity_score": 0.9, "reasoning": "재감지, 심각도 갱신"})

    assert first_id == second_id
    assert await pool.fetchval("SELECT count(*) FROM document.decision_conflicts") == 1
    row = await pool.fetchrow("SELECT severity_score, reasoning FROM document.decision_conflicts WHERE id = $1", first_id)
    assert (float(row["severity_score"]), row["reasoning"]) == (0.9, "재감지, 심각도 갱신")


async def test_different_clause_from_same_pair_is_a_new_row(pool, add_document):
    source_id = await add_document()
    target_id = await add_document()
    db = Db(pool)
    kwargs = dict(
        source_doc_id=source_id, target_doc_id=target_id, conflict_type="DIRECT_CONTRADICTION",
        severity_score=0.7, target_clause="기존 조항", reasoning="사유", affected_teams=[],
    )

    await db.record_conflict(source_clause="조항 A", **kwargs)
    await db.record_conflict(source_clause="조항 B", **kwargs)

    assert await pool.fetchval("SELECT count(*) FROM document.decision_conflicts") == 2


async def test_find_document_with_content_uses_latest_version(pool, add_document):
    doc_id = await add_document(content="현재 본문", organization_id=7, team_id=3, current_version=2)
    await pool.execute(
        "INSERT INTO document.document_version (document_id, version, content) VALUES ($1, 1, '구버전'), ($1, 2, '신버전')",
        doc_id,
    )
    db = Db(pool)

    found = await db.find_document_with_content(doc_id)

    assert found["organization_id"] == 7 and found["team_id"] == 3
    assert found["content"] == "신버전"
    # 상충을 어느 판 기준으로 기록할지 정하려면 current_version 이 같이 실려야 한다
    assert found["current_version"] == 2
    assert await db.find_document_with_content(999999) is None


# ---- 문서를 개정하면 이전 판 기준 OPEN 상충을 닫는다 ----


async def _conflict(pool, source_id, target_id, *, clause: str, version: int | None, status: str = "OPEN") -> int:
    conflict_id = await Db(pool).record_conflict(
        source_doc_id=source_id,
        target_doc_id=target_id,
        conflict_type="DIRECT_CONTRADICTION",
        severity_score=0.8,
        source_clause=clause,
        target_clause="기존 조항",
        reasoning="사유",
        affected_teams=[],
        source_doc_version=version,
    )
    if status != "OPEN":
        await pool.execute(
            "UPDATE document.decision_conflicts SET status = $2 WHERE id = $1", conflict_id, status
        )
    return conflict_id


async def test_record_conflict_stamps_source_doc_version(pool, add_document):
    source_id = await add_document(current_version=3)
    target_id = await add_document()

    conflict_id = await _conflict(pool, source_id, target_id, clause="조항", version=3)

    assert await pool.fetchval(
        "SELECT source_doc_version FROM document.decision_conflicts WHERE id = $1", conflict_id
    ) == 3


async def test_supersede_closes_only_open_conflicts_from_older_versions(pool, add_document):
    source_id = await add_document(current_version=2)
    target_id = await add_document()
    db = Db(pool)

    stale = await _conflict(pool, source_id, target_id, clause="1판 조항", version=1)
    current = await _conflict(pool, source_id, target_id, clause="2판 조항", version=2)
    # 사람이 이미 처리한 건은 판이 낡았어도 건드리지 않는다
    handled = await _conflict(pool, source_id, target_id, clause="1판 조항(처리됨)", version=1, status="RESOLVED")
    # 컬럼이 생기기 전 기록은 어느 판인지 몰라 그대로 둔다
    unknown = await _conflict(pool, source_id, target_id, clause="판 불명 조항", version=None)

    count = await db.supersede_outdated_conflicts(source_id, 2)

    assert count == 1
    statuses = {
        row["id"]: row["status"]
        for row in await pool.fetch("SELECT id, status FROM document.decision_conflicts")
    }
    assert statuses[stale] == "SUPERSEDED"
    assert statuses[current] == "OPEN"
    assert statuses[handled] == "RESOLVED"
    assert statuses[unknown] == "OPEN"


async def test_supersede_leaves_other_documents_alone(pool, add_document):
    source_id = await add_document(current_version=2)
    other_id = await add_document(current_version=2)
    target_id = await add_document()
    db = Db(pool)

    mine = await _conflict(pool, source_id, target_id, clause="내 1판 조항", version=1)
    theirs = await _conflict(pool, other_id, target_id, clause="남의 1판 조항", version=1)

    assert await db.supersede_outdated_conflicts(source_id, 2) == 1
    statuses = {
        row["id"]: row["status"]
        for row in await pool.fetch("SELECT id, status FROM document.decision_conflicts")
    }
    assert (statuses[mine], statuses[theirs]) == ("SUPERSEDED", "OPEN")


# ─── 작성자 개인 알림 대상 조회 ────────────


async def test_find_document_author_emails_returns_active_authors(pool, add_document, add_user):
    db = Db(pool)
    author_a = await add_user(email="a@company.test", name="김하늘")
    author_b = await add_user(email="b@company.test", name="이도윤")
    doc_a = await add_document(title="재택근무 지침", author_id=author_a)
    doc_b = await add_document(title="출장비 규정", author_id=author_b)

    emails = await db.find_document_author_emails([doc_a, doc_b])

    assert sorted(emails) == ["a@company.test", "b@company.test"]


async def test_find_document_author_emails_dedupes_same_author(pool, add_document, add_user):
    """source·target 을 같은 사람이 썼으면 한 번만 나와야 한다 — 두 통 보내지 않는다."""
    db = Db(pool)
    author = await add_user(email="one@company.test")
    doc_a = await add_document(title="문서 A", author_id=author)
    doc_b = await add_document(title="문서 B", author_id=author)

    assert await db.find_document_author_emails([doc_a, doc_b]) == ["one@company.test"]


async def test_find_document_author_emails_skips_null_author(pool, add_document, add_user):
    """일괄 가져오기 등 계정과 연결되지 않은 문서는 대상이 없다."""
    db = Db(pool)
    doc = await add_document(title="가져온 문서", author_id=None)

    assert await db.find_document_author_emails([doc]) == []


async def test_find_document_author_emails_skips_disabled_accounts(pool, add_document, add_user):
    """퇴사자 메일함으로 보내지 않는다."""
    db = Db(pool)
    left = await add_user(email="left@company.test", status="DISABLED")
    doc = await add_document(title="퇴사자가 쓴 문서", author_id=left)

    assert await db.find_document_author_emails([doc]) == []


async def test_find_document_author_emails_empty_input_skips_query(pool):
    db = Db(pool)

    assert await db.find_document_author_emails([]) == []

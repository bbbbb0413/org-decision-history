import pytest
from unittest.mock import AsyncMock, MagicMock

from app.conflict_analyzer import ConflictAnalyzer
from app.db import RecordedConflict
from app.detector import ConflictDetector, pack_windows
from app.models import SearchHit
from app.notifications.dispatcher import NotificationDispatcher
from app.settings import Settings


class MockGenerator:
    def __init__(self, response_text: str):
        self.response_text = response_text

    async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 1000) -> str:
        return self.response_text


@pytest.mark.asyncio
async def test_assertion_extraction():
    mock_json = """
    ```json
    [
        "임직원은 주 2회 재택근무를 신청할 수 있다.",
        "외부 통신 시 모든 연동에 mTLS를 적용해야 한다."
    ]
    ```
    """
    analyzer = ConflictAnalyzer(MockGenerator(mock_json))
    doc_text = "우리 회사의 새로운 원격 근무 및 보안 가이드라인입니다. ..."
    assertions = await analyzer.extract_assertions(doc_text)

    assert len(assertions) == 2
    assert "재택근무" in assertions[0]
    assert "mTLS" in assertions[1]


@pytest.mark.asyncio
async def test_nli_conflict_analysis_contradiction():
    mock_json = """
    {
        "has_conflict": true,
        "conflict_score": 0.88,
        "conflict_type": "DIRECT_CONTRADICTION",
        "reasoning": "신규 문서는 HTTP Basic Auth를 허용하지만 기존 규정은 mTLS를 필수로 규정합니다.",
        "affected_teams": ["인프라보안팀", "결제개발팀"],
        "recommended_action": "보안팀 승인 필요"
    }
    """
    analyzer = ConflictAnalyzer(MockGenerator(mock_json))
    res = await analyzer.analyze_contradiction(
        new_doc_id=100,
        new_doc_title="RFC-104",
        new_clause="외부 연동 시 HTTP Basic Auth를 사용한다.",
        existing_doc_id=45,
        existing_doc_title="보안 통신 규정",
        existing_clause="모든 외부 통신에는 mTLS 암호화가 필수이다.",
    )

    assert res is not None
    assert res.severity_score == 0.88
    assert res.conflict_type == "DIRECT_CONTRADICTION"
    assert "인프라보안팀" in res.impacted_teams
    assert res.target_doc_id == 45


@pytest.mark.asyncio
async def test_nli_conflict_analysis_no_conflict():
    mock_json = """
    {
        "has_conflict": false,
        "conflict_score": 0.0,
        "conflict_type": "NONE",
        "reasoning": "두 조항 간 내용상 상충 없음",
        "affected_teams": [],
        "recommended_action": "없음"
    }
    """
    analyzer = ConflictAnalyzer(MockGenerator(mock_json))
    res = await analyzer.analyze_contradiction(
        new_doc_id=100,
        new_doc_title="복지 규정 개정안",
        new_clause="점심 식대는 12,000원까지 지원된다.",
        existing_doc_id=12,
        existing_doc_title="근무 규정",
        existing_clause="원격 근무는 주 2회 가능하다.",
    )

    assert res is None


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["false", '"false"', "null"])
async def test_nli_not_a_conflict_even_if_score_is_high(verdict):
    """판정이 "상충 아님"이면 점수가 높아도 버린다.

    `not has_conflict and score < 0.5` 같은 조건이면 "아님"인데 점수가 0.5 이상일 때 상충 후보로 넘어가,
    기록 기준(0.70)을 넘으면 실제로 기록·알림한다. 문자열 "false" 도 bool() 로 참이 돼 상충이 되지 않아야 한다.
    """
    mock_json = f"""
    {{
        "has_conflict": {verdict},
        "conflict_score": 0.9,
        "conflict_type": "NONE",
        "reasoning": "상충 없음",
        "affected_teams": [],
        "recommended_action": "없음"
    }}
    """
    res = await ConflictAnalyzer(MockGenerator(mock_json)).analyze_contradiction(
        new_doc_id=100, new_doc_title="신규", new_clause="조항 A",
        existing_doc_id=12, existing_doc_title="기존", existing_clause="조항 B",
    )
    assert res is None


@pytest.mark.asyncio
async def test_nli_score_is_clamped_to_unit_range():
    mock_json = '{"has_conflict": true, "conflict_score": 1.7, "conflict_type": "DIRECT_CONTRADICTION"}'
    res = await ConflictAnalyzer(MockGenerator(mock_json)).analyze_contradiction(
        new_doc_id=100, new_doc_title="신규", new_clause="조항 A",
        existing_doc_id=12, existing_doc_title="기존", existing_clause="조항 B",
    )
    assert res is not None and res.severity_score == 1.0


@pytest.mark.asyncio
async def test_detector_detect_and_record():
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])

    docs = {
        100: {
            "id": 100,
            "title": "신규 통신 표준 RFC",
            "organization_id": 1,
            "team_id": 2,
            "decision_status": "CURRENT",
            "authored_at": "2026-09-11",
            "content": "외부 서비스와 연동할 때 HTTP Basic Auth 방식을 사용한다.",
        },
        50: {
            "id": 50,
            "title": "전사 보안 가이드라인",
            "organization_id": 1,
            "team_id": 2,
            "decision_status": "CURRENT",
            "authored_at": "2025-01-01",
            "content": "모든 외부 통신에는 mTLS 암호화가 필수이다.",
        },
    }

    mock_db.find_document_with_content = AsyncMock(side_effect=lambda doc_id: docs.get(doc_id))
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(1, created=True, status="OPEN", notify=True))

    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1] * 768)

    mock_vector_client = MagicMock()
    mock_vector_client.search_points = AsyncMock(return_value=[
        SearchHit(
            document_id=50,
            document_version=1,
            chunk_no=0,
            content="모든 외부 통신에는 mTLS 암호화가 필수이다.",
            section_path="",
            chunk_tag="",
            score=0.92,
            vector_similarity=0.92,
        )
    ])

    # 1회차: 조항 추출, 2회차: 상충 분석
    responses = [
        '["외부 서비스와 연동할 때 HTTP Basic Auth 방식을 사용한다."]',
        """
        {
            "has_conflict": true,
            "conflict_score": 0.85,
            "conflict_type": "DIRECT_CONTRADICTION",
            "reasoning": "신규 문서는 Basic Auth를 요구하나 기존 규정은 mTLS를 의무화함",
            "affected_teams": ["보안팀"],
            "recommended_action": "보안팀과 사전 협의 필요"
        }
        """,
    ]

    class SequentialMockGenerator:
        def __init__(self, resps):
            self.resps = list(resps)
            self.idx = 0

        async def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 1000) -> str:
            text = self.resps[self.idx] if self.idx < len(self.resps) else self.resps[-1]
            self.idx += 1
            return text

    detector = ConflictDetector(
        db=mock_db,
        embedder=mock_embedder,
        vector_client=mock_vector_client,
        generator=SequentialMockGenerator(responses),
        threshold=0.70,
    )

    conflicts = await detector.detect_and_record(100, 1)

    assert len(conflicts) == 1
    assert conflicts[0].severity_score == 0.85
    assert conflicts[0].target_doc_id == 50
    mock_db.record_conflict.assert_awaited_once()
    # 등급은 기록할 때 정해 함께 저장한다
    assert mock_db.record_conflict.call_args.kwargs["severity_level"] == "CRITICAL"


# ─── 팀별 알림 채널 라우팅 ────────────

def _sample_settings() -> Settings:
    return Settings(smtp_host="mock", smtp_port=587, email_from="noreply@company.com")


@pytest.mark.asyncio
async def test_resolve_team_channels_skips_none_and_unset_team_ids():
    """팀 id가 없거나(None), team_notification_channel 행이 아예 없는 팀은 그대로 둔다 —
    전역 설정 채널로만 발송한다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_team_channels = AsyncMock(return_value={})

    detector = ConflictDetector(
        db=mock_db, embedder=MagicMock(), vector_client=MagicMock(),
        generator=MagicMock(), settings=_sample_settings(),
    )

    channels, notified = await detector._resolve_team_channels({None, 7})

    assert channels == []
    assert notified == set()
    mock_db.find_team_channels.assert_awaited_once_with([7])


@pytest.mark.asyncio
async def test_resolve_team_channels_builds_configured_channels():
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_team_channels = AsyncMock(return_value={
        7: {
            "slack_webhook_url": "https://hooks.slack.test/a",
            "teams_webhook_url": None,
            "notification_email": "lead@team.test, member@team.test",
        }
    })
    mock_db.find_team_member_emails = AsyncMock(return_value=[])  # notification_email이 있어 호출 안 됨

    detector = ConflictDetector(
        db=mock_db, embedder=MagicMock(), vector_client=MagicMock(),
        generator=MagicMock(), settings=_sample_settings(),
    )

    channels, notified = await detector._resolve_team_channels({7})

    assert [c.name for c in channels] == ["slack", "email"]
    # 작성자 중복 발송을 빼는 데 쓰는 집합
    assert notified == {"lead@team.test", "member@team.test"}
    mock_db.find_team_member_emails.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_team_channels_falls_back_to_member_emails():
    """행은 있는데 notification_email이 비어 있으면 팀원 이메일로 폴백한다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_team_channels = AsyncMock(return_value={
        7: {"slack_webhook_url": None, "teams_webhook_url": None, "notification_email": None}
    })
    mock_db.find_team_member_emails = AsyncMock(return_value=["a@team.test", "b@team.test"])

    detector = ConflictDetector(
        db=mock_db, embedder=MagicMock(), vector_client=MagicMock(),
        generator=MagicMock(), settings=_sample_settings(),
    )

    channels, notified = await detector._resolve_team_channels({7})

    assert [c.name for c in channels] == ["email"]
    assert notified == {"a@team.test", "b@team.test"}
    mock_db.find_team_member_emails.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_detect_and_record_dispatches_with_team_channels():
    """상충 감지 시 source/target 문서의 team_id로 조회한 팀 채널이 dispatch 의
    extra_channels 로 실제로 전달되는지 확인한다(전역 채널과 별개로 병행)."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    docs = {
        100: {
            "id": 100, "title": "신규 통신 표준 RFC", "organization_id": 1, "team_id": 2,
            "decision_status": "CURRENT", "authored_at": "2026-09-11",
            "content": "외부 서비스와 연동할 때 HTTP Basic Auth 방식을 사용한다.",
        },
        50: {
            "id": 50, "title": "전사 보안 가이드라인", "organization_id": 1, "team_id": 3,
            "decision_status": "CURRENT", "authored_at": "2025-01-01",
            "content": "모든 외부 통신에는 mTLS 암호화가 필수이다.",
        },
    }
    mock_db.find_document_with_content = AsyncMock(side_effect=lambda doc_id: docs.get(doc_id))
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(1, created=True, status="OPEN", notify=True))
    mock_db.find_team_channels = AsyncMock(return_value={
        2: {"slack_webhook_url": "https://hooks.slack.test/source", "teams_webhook_url": None, "notification_email": None},
    })
    mock_db.find_team_member_emails = AsyncMock(return_value=[])
    # 작성자 개인 알림은 이 테스트의 관심사가 아니라 비워 둔다 (전용 테스트가 아래에 있다)
    mock_db.find_document_author_emails = AsyncMock(return_value=[])

    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1] * 768)

    mock_vector_client = MagicMock()
    mock_vector_client.search_points = AsyncMock(return_value=[
        SearchHit(document_id=50, document_version=1, chunk_no=0,
                   content="모든 외부 통신에는 mTLS 암호화가 필수이다.", section_path="", chunk_tag="",
                   score=0.92, vector_similarity=0.92)
    ])

    class SequentialMockGenerator:
        def __init__(self, resps):
            self.resps = list(resps)
            self.idx = 0

        async def complete(self, system_prompt, user_prompt, max_tokens=1000):
            text = self.resps[self.idx] if self.idx < len(self.resps) else self.resps[-1]
            self.idx += 1
            return text

    responses = [
        '["외부 서비스와 연동할 때 HTTP Basic Auth 방식을 사용한다."]',
        """
        {
            "has_conflict": true,
            "conflict_score": 0.85,
            "conflict_type": "DIRECT_CONTRADICTION",
            "reasoning": "신규 문서는 Basic Auth를 요구하나 기존 규정은 mTLS를 의무화함",
            "affected_teams": ["보안팀"],
            "recommended_action": "보안팀과 사전 협의 필요"
        }
        """,
    ]

    dispatcher = NotificationDispatcher(channels=[], min_severity=0.70)
    dispatcher.dispatch = AsyncMock(return_value={})

    detector = ConflictDetector(
        db=mock_db, embedder=mock_embedder, vector_client=mock_vector_client,
        generator=SequentialMockGenerator(responses), threshold=0.70,
        dispatcher=dispatcher, settings=_sample_settings(),
    )

    # get_active_channels()가 빈 리스트를 주는 dispatcher라도 dispatch는 호출된다 —
    # detect_and_record는 dispatcher 존재 여부만 보고, 실제 발송 여부는 dispatch 내부(extra_channels 포함)가 정한다.
    # 여기서는 dispatch 자체를 목으로 바꿔 extra_channels 인자를 검증한다.
    await detector.detect_and_record(100, 1)

    dispatcher.dispatch.assert_awaited_once()
    _, kwargs = dispatcher.dispatch.call_args
    extra = kwargs["extra_channels"]
    assert [c.name for c in extra] == ["slack"]
    mock_db.find_team_channels.assert_awaited_once_with([2, 3])


# ─── 작성자 개인 알림 ────────────

def _author_detector(mock_db) -> ConflictDetector:
    return ConflictDetector(
        db=mock_db, embedder=MagicMock(), vector_client=MagicMock(),
        generator=MagicMock(), settings=_sample_settings(),
    )


@pytest.mark.asyncio
async def test_author_channel_sends_to_document_authors():
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_author_emails = AsyncMock(return_value=["author@company.test"])

    channels = await _author_detector(mock_db)._resolve_author_channels([100, 50], already_notified=set())

    assert [c.name for c in channels] == ["email"]
    assert channels[0].is_enabled()
    mock_db.find_document_author_emails.assert_awaited_once_with([100, 50])


@pytest.mark.asyncio
async def test_author_channel_skips_addresses_the_team_already_notified():
    """작성자가 그 팀 소속이면 팀 채널에서 이미 같은 메일을 받는다 — 두 통 보내지 않는다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_author_emails = AsyncMock(
        return_value=["author@company.test", "other@company.test"])

    channels = await _author_detector(mock_db)._resolve_author_channels(
        [100], already_notified={"author@company.test"})

    assert len(channels) == 1
    # 남은 한 명에게만 간다
    assert channels[0]._email_to_list == ["other@company.test"]


@pytest.mark.asyncio
async def test_author_channel_empty_when_everyone_already_notified():
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_author_emails = AsyncMock(return_value=["author@company.test"])

    channels = await _author_detector(mock_db)._resolve_author_channels(
        [100], already_notified={"author@company.test"})

    assert channels == []


@pytest.mark.asyncio
async def test_author_channel_empty_when_no_author():
    """author_id 가 NULL 인 문서(일괄 가져오기 등)만 걸린 상충은 개인 알림 대상이 없다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_author_emails = AsyncMock(return_value=[])

    channels = await _author_detector(mock_db)._resolve_author_channels([100], already_notified=set())

    assert channels == []


@pytest.mark.asyncio
async def test_author_channel_needs_smtp_settings():
    """settings 가 없으면 SMTP 자격증명을 못 만든다 — DB 를 건드리지도 않는다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_author_emails = AsyncMock(return_value=["author@company.test"])

    detector = ConflictDetector(
        db=mock_db, embedder=MagicMock(), vector_client=MagicMock(),
        generator=MagicMock(), settings=None,
    )

    assert await detector._resolve_author_channels([100], already_notified=set()) == []
    mock_db.find_document_author_emails.assert_not_called()

# ─── 개정 전 판 기준 상충 정리 ────────────

def _detector_with_doc(mock_db, *, assertions_json: str, analysis_json: str | None, dispatcher=None):
    """조항 추출 → (있으면) 상충 분석 순으로 답하는 목 LLM 을 물린 detector."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query = AsyncMock(return_value=[0.1] * 768)
    mock_vector_client = MagicMock()
    mock_vector_client.search_points = AsyncMock(return_value=[
        SearchHit(document_id=50, document_version=1, chunk_no=0,
                  content="모든 외부 통신에는 mTLS 암호화가 필수이다.", section_path="", chunk_tag="",
                  score=0.92, vector_similarity=0.92)
    ])

    class SequentialMockGenerator:
        def __init__(self, resps):
            self.resps = list(resps)
            self.idx = 0

        async def complete(self, system_prompt, user_prompt, max_tokens=1000):
            text = self.resps[self.idx] if self.idx < len(self.resps) else self.resps[-1]
            self.idx += 1
            return text

    resps = [assertions_json] + ([analysis_json] if analysis_json else [])
    return ConflictDetector(
        db=mock_db, embedder=mock_embedder, vector_client=mock_vector_client,
        generator=SequentialMockGenerator(resps), threshold=0.70, dispatcher=dispatcher,
    )


_NO_CONFLICT_JSON = """
{
    "has_conflict": false, "conflict_score": 0.0, "conflict_type": "NONE",
    "reasoning": "상충 없음", "affected_teams": [], "recommended_action": "없음"
}
"""


def _revised_doc(version: int | None) -> dict:
    return {
        "id": 100, "title": "개정된 통신 표준", "organization_id": 1, "team_id": 2,
        "decision_status": "CURRENT", "authored_at": "2026-09-11", "current_version": version,
        "content": "외부 연동에는 mTLS 를 쓴다.",
    }


@pytest.mark.asyncio
async def test_detect_and_record_stamps_and_supersedes_by_version():
    """재감지한 상충에는 현재 판을 새기고, 재감지를 마친 뒤 옛 판 기준 OPEN 상충을 닫는다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_with_content = AsyncMock(side_effect=lambda doc_id: {
        100: _revised_doc(4),
        50: {"id": 50, "title": "전사 보안 가이드라인", "organization_id": 1, "team_id": 3,
             "decision_status": "CURRENT", "authored_at": "2025-01-01", "current_version": 1,
             "content": "모든 외부 통신에는 mTLS 암호화가 필수이다."},
    }.get(doc_id))
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(1, created=True, status="OPEN", notify=True))
    mock_db.supersede_outdated_conflicts = AsyncMock(return_value=2)

    detector = _detector_with_doc(
        mock_db,
        assertions_json='["외부 연동에는 mTLS 를 쓴다."]',
        analysis_json="""
        {
            "has_conflict": true, "conflict_score": 0.85, "conflict_type": "DIRECT_CONTRADICTION",
            "reasoning": "사유", "affected_teams": [], "recommended_action": "협의"
        }
        """,
    )

    await detector.detect_and_record(100, 1)

    assert mock_db.record_conflict.call_args.kwargs["source_doc_version"] == 4
    mock_db.supersede_outdated_conflicts.assert_awaited_once_with(100, 4)


@pytest.mark.asyncio
async def test_supersede_runs_even_when_no_new_conflict_is_found():
    """개정 후 상충이 사라진 경우야말로 옛 OPEN 상충이 남으면 안 된다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_with_content = AsyncMock(side_effect=lambda doc_id: {
        100: _revised_doc(4),
        50: {"id": 50, "title": "전사 보안 가이드라인", "organization_id": 1, "team_id": 3,
             "decision_status": "CURRENT", "authored_at": "2025-01-01", "current_version": 1,
             "content": "모든 외부 통신에는 mTLS 암호화가 필수이다."},
    }.get(doc_id))
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(1, created=True, status="OPEN", notify=True))
    mock_db.supersede_outdated_conflicts = AsyncMock(return_value=1)

    detector = _detector_with_doc(
        mock_db,
        assertions_json='["외부 연동에는 mTLS 를 쓴다."]',
        analysis_json=_NO_CONFLICT_JSON,
    )

    conflicts = await detector.detect_and_record(100, 1)

    assert conflicts == []
    mock_db.record_conflict.assert_not_called()
    mock_db.supersede_outdated_conflicts.assert_awaited_once_with(100, 4)


@pytest.mark.asyncio
async def test_supersede_is_skipped_when_clause_extraction_fails():
    """LLM 이 조항을 하나도 못 뽑은 판으로는 멀쩡한 상충을 닫지 않는다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_with_content = AsyncMock(return_value=_revised_doc(4))
    mock_db.supersede_outdated_conflicts = AsyncMock(return_value=0)

    detector = _detector_with_doc(mock_db, assertions_json="[]", analysis_json=None)

    assert await detector.detect_and_record(100, 1) == []
    mock_db.supersede_outdated_conflicts.assert_not_called()


@pytest.mark.asyncio
async def test_supersede_is_skipped_when_document_has_no_version():
    """current_version 을 못 읽으면(옛 데이터) 비교 기준이 없으므로 아무것도 닫지 않는다."""
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_with_content = AsyncMock(return_value=_revised_doc(None))
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(1, created=True, status="OPEN", notify=True))
    mock_db.supersede_outdated_conflicts = AsyncMock(return_value=0)

    detector = _detector_with_doc(
        mock_db, assertions_json='["외부 연동에는 mTLS 를 쓴다."]', analysis_json=_NO_CONFLICT_JSON,
    )

    await detector.detect_and_record(100, 1)

    mock_db.supersede_outdated_conflicts.assert_not_called()


# ─── 알림은 새로 기록한 상충만 (같은 문서 재검사·같은 이벤트 재처리) ────────────

_CONFLICT_JSON = """
{
    "has_conflict": true, "conflict_score": 0.9, "conflict_type": "DIRECT_CONTRADICTION",
    "reasoning": "상충", "affected_teams": [], "recommended_action": "검토"
}
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("notify, notified", [(True, 1), (False, 0)])
async def test_only_conflicts_marked_for_notification_are_notified(notify, notified):
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=[])
    mock_db.find_document_with_content = AsyncMock(return_value=_revised_doc(2))
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(7, created=True, status="OPEN", notify=notify))
    mock_db.supersede_outdated_conflicts = AsyncMock(return_value=0)
    dispatcher = NotificationDispatcher(channels=[], min_severity=0.70)
    dispatcher.dispatch = AsyncMock(return_value={})
    detector = _detector_with_doc(
        mock_db, assertions_json='["외부 연동에는 mTLS 를 쓴다."]', analysis_json=_CONFLICT_JSON, dispatcher=dispatcher
    )
    detector._resolve_team_channels = AsyncMock(return_value=([], set()))
    detector._resolve_author_channels = AsyncMock(return_value=[])

    await detector.detect_and_record(100, 1)

    mock_db.record_conflict.assert_awaited_once()
    assert dispatcher.dispatch.await_count == notified


# ─── 상충 검사 원문: 색인 청크를 창으로 나눠 검사한다 ────────────

class RecordingExtractor:
    """조항 추출 프롬프트를 기록하고 창마다 정해 둔 조항을 돌려준다."""

    def __init__(self, answers: list[str]):
        self.answers = answers
        self.prompts: list[str] = []

    async def complete(self, system_prompt, user_prompt, max_tokens=1000):
        self.prompts.append(user_prompt)
        return self.answers[min(len(self.prompts), len(self.answers)) - 1]


def _window_detector(mock_db, generator, **limits) -> ConflictDetector:
    vector_client = MagicMock()
    vector_client.search_points = AsyncMock(return_value=[])  # 후보가 없어 조항 추출까지만 본다
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 768)
    return ConflictDetector(db=mock_db, embedder=embedder, vector_client=vector_client, generator=generator, **limits)


def _db_with_chunks(chunks: list[str]) -> MagicMock:
    mock_db = MagicMock()
    mock_db.find_source_chunks = AsyncMock(return_value=chunks)
    mock_db.find_document_with_content = AsyncMock(return_value=_revised_doc(2))
    mock_db.supersede_outdated_conflicts = AsyncMock(return_value=0)
    return mock_db


@pytest.mark.asyncio
async def test_clauses_are_extracted_from_indexed_chunks_not_form_content():
    generator = RecordingExtractor(['["첫 창 조항"]', '["둘째 창 조항", "첫 창 조항"]'])
    detector = _window_detector(_db_with_chunks(["가" * 3000, "나" * 3000, "다" * 100]), generator, window_chars=4000)

    await detector.detect_and_record(100, 1)

    # 앞 4,000자만 보던 때와 달리 문서 끝(다)까지 검사한다. 청크는 창 경계에서 자르지 않는다
    assert len(generator.prompts) == 2
    assert "가" * 3000 in generator.prompts[0] and "나" * 3000 in generator.prompts[1] and "다" * 100 in generator.prompts[1]
    assert all("외부 연동에는 mTLS 를 쓴다." not in p for p in generator.prompts), "폼 본문이 아니라 색인 청크를 쓴다"
    # 이웃 창에서 다시 나온 조항은 한 번만 검색한다
    assert detector._embedder.embed_query.await_count == 2


@pytest.mark.asyncio
async def test_form_content_is_used_when_document_has_no_index_yet():
    generator = RecordingExtractor(['["조항"]'])
    detector = _window_detector(_db_with_chunks([]), generator)

    await detector.detect_and_record(100, 1)

    [prompt] = generator.prompts
    assert "외부 연동에는 mTLS 를 쓴다." in prompt


@pytest.mark.asyncio
async def test_windows_and_clauses_are_capped():
    generator = RecordingExtractor(['["조항 1", "조항 2", "조항 3"]', '["조항 4", "조항 5", "조항 6"]'])
    detector = _window_detector(
        _db_with_chunks(["가" * 3000, "나" * 3000, "다" * 3000]), generator,
        window_chars=4000, max_windows=2, max_assertions=4,
    )

    await detector.detect_and_record(100, 1)

    assert len(generator.prompts) == 2, "창 상한을 넘는 뒷부분은 검사하지 않는다"
    assert detector._embedder.embed_query.await_count == 4, "조항 상한까지만 후보를 찾는다"


def test_pack_windows_keeps_chunks_whole_and_splits_only_oversized_ones():
    assert pack_windows(["a" * 3, "b" * 3, "c" * 3], 8) == ["aaa\n\nbbb", "ccc"]
    assert pack_windows(["x" * 10], 4) == ["xxxx", "xxxx", "xx"]
    assert pack_windows(["", "  "], 4) == []


# ─── 삭제된 문서는 지식 그래프에서 뺀다 ────────────

@pytest.mark.asyncio
async def test_forget_document_removes_graph_node():
    sync = MagicMock()
    sync.remove_document = AsyncMock(return_value=1)
    detector = ConflictDetector(
        db=MagicMock(), embedder=MagicMock(), vector_client=MagicMock(), generator=MagicMock(), sync_service=sync
    )

    await detector.forget_document(100)

    sync.remove_document.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_forget_document_without_graph_is_a_no_op():
    detector = ConflictDetector(db=MagicMock(), embedder=MagicMock(), vector_client=MagicMock(), generator=MagicMock())

    await detector.forget_document(100)


@pytest.mark.asyncio
async def test_graph_sync_uses_the_same_indexed_windows_as_clause_extraction():
    generator = RecordingExtractor(['["조항"]'])
    sync = MagicMock()
    sync.sync_document = AsyncMock()
    detector = _window_detector(_db_with_chunks(["가" * 3000, "나" * 3000]), generator, window_chars=4000)
    detector._sync_service = sync

    await detector.detect_and_record(100, 1)

    kwargs = sync.sync_document.await_args.kwargs
    assert kwargs["sources"] == ["가" * 3000, "나" * 3000] and kwargs["extract_max_chars"] == 4000
    assert kwargs["content"] == "외부 연동에는 mTLS 를 쓴다.", "노드 요약은 폼 본문에서 만든다"



@pytest.mark.asyncio
async def test_record_carries_hashes_of_source_window_and_target_chunk():
    from app.detector import text_hash

    mock_db = _db_with_chunks(["가" * 3000, "나" * 3000])
    mock_db.record_conflict = AsyncMock(return_value=RecordedConflict(1, created=True, status="OPEN", notify=False))
    target = SearchHit(document_id=50, document_version=3, chunk_no=0, content="모든 외부 통신에는 mTLS 가 필수다.",
                       section_path="", chunk_tag="", score=0.9, vector_similarity=0.9)
    vector_client = MagicMock()
    vector_client.search_points = AsyncMock(return_value=[target])
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 768)
    # 첫 창에서는 조항이 없고 둘째 창에서 조항이 나온다
    generator = RecordingExtractor(['[]', '["둘째 창 조항"]', _CONFLICT_JSON])
    detector = ConflictDetector(db=mock_db, embedder=embedder, vector_client=vector_client, generator=generator,
                                window_chars=4000)

    await detector.detect_and_record(100, 1)

    kwargs = mock_db.record_conflict.await_args.kwargs
    assert kwargs["source_text_hash"] == text_hash("나" * 3000), "조항을 뽑은 창의 해시"
    assert kwargs["target_text_hash"] == text_hash("모든 외부 통신에는 mTLS 가 필수다.")
    assert kwargs["target_doc_version"] == 3


# ─── LLM 일시 불가(429 등)는 "결과 없음"이 아니라 재시도 대상 ────────────

class UnavailableGenerator:
    """처음 n 번은 응답하고 그 뒤로는 한도 초과로 실패한다."""

    def __init__(self, answers: list[str]):
        self.answers = list(answers)

    async def complete(self, system_prompt, user_prompt, max_tokens=1000):
        from history_shared.llm import LlmUnavailableError

        if not self.answers:
            raise LlmUnavailableError("429 Too Many Requests")
        return self.answers.pop(0)


@pytest.mark.asyncio
async def test_clause_extraction_does_not_fall_back_when_llm_is_unavailable():
    from history_shared.llm import LlmUnavailableError

    with pytest.raises(LlmUnavailableError):
        await ConflictAnalyzer(UnavailableGenerator([])).extract_assertions("모든 외부 통신은 mTLS 를 필수로 한다.")


@pytest.mark.asyncio
async def test_contradiction_check_does_not_read_unavailable_as_no_conflict():
    from history_shared.llm import LlmUnavailableError

    with pytest.raises(LlmUnavailableError):
        await ConflictAnalyzer(UnavailableGenerator([])).analyze_contradiction(1, "a", "조항", 2, "b", "기존 조항")


@pytest.mark.asyncio
async def test_unavailable_llm_fails_detection_without_superseding():
    """판정이 끝나지 않았으므로 예외로 올려 재시도에 맡기고, 개정 전 판정 정리(SUPERSEDED)도 돌리지 않는다."""
    from history_shared.llm import LlmUnavailableError

    mock_db = _db_with_chunks(["외부 연동에는 mTLS 를 쓴다."])
    vector_client = MagicMock()
    vector_client.search_points = AsyncMock(return_value=[
        SearchHit(document_id=50, document_version=1, chunk_no=0, content="모든 외부 통신에는 mTLS 가 필수다.",
                  section_path="", chunk_tag="", score=0.9, vector_similarity=0.9)
    ])
    embedder = MagicMock()
    embedder.embed_query = AsyncMock(return_value=[0.1] * 768)
    detector = ConflictDetector(db=mock_db, embedder=embedder, vector_client=vector_client,
                                generator=UnavailableGenerator(['["외부 연동에는 mTLS 를 쓴다."]']))

    with pytest.raises(LlmUnavailableError):
        await detector.detect_and_record(100, 1)

    mock_db.supersede_outdated_conflicts.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["JSON 아님", '{"has_conflict": false}', ""])
async def test_unparseable_extraction_does_not_fall_back_to_keywords(reply):
    """응답을 해석하지 못하면 '한다'·'필수' 같은 낱말로 문장을 골라 대신 판정하지 않는다. 빈 응답은 LLM 키가 없을 때다."""
    window = "[문서] 보안 정책 | 작성일 2026-01-01 | 절 결정\n\n외부 연동에는 반드시 mTLS 를 사용하여야 한다.\n"

    assert await ConflictAnalyzer(MockGenerator(reply)).extract_assertions(window) == []

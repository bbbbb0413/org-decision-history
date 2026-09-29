"""정책 및 의사결정 상충 감지기(Conflict Detector)."""
import structlog
from typing import Optional

from history_shared.embeddings import Embedder
from history_shared.metrics import CONFLICT_DETECTIONS, CONFLICTS_RECORDED, CONFLICTS_SUPERSEDED

from app.conflict_analyzer import ConflictAnalyzer
from app.db import Db
from app.generation import AnswerGenerator
from history_shared.graph.sync import GraphSyncService
from app.models import ConflictDetectionResult
from app.notifications.channels.base import NotificationChannel
from app.notifications.channels.email import EmailNotificationChannel
from app.notifications.channels.slack import SlackNotificationChannel
from app.notifications.channels.teams import TeamsNotificationChannel
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.models import ConflictNotificationPayload
from app.settings import Settings
from app.vector_retriever import VectorSearchClient

log = structlog.get_logger()

DEFAULT_CONFLICT_THRESHOLD = 0.70
# 조항 하나를 임베딩할 때 넣는 최대 글자 수
_EMBED_TEXT_LIMIT = 2000


class ConflictDetector:
    def __init__(
        self,
        db: Db,
        embedder: Embedder,
        vector_client: VectorSearchClient,
        generator: AnswerGenerator,
        threshold: float = DEFAULT_CONFLICT_THRESHOLD,
        dispatcher: Optional[NotificationDispatcher] = None,
        frontend_base_url: str = "http://localhost:5173",
        sync_service: Optional[GraphSyncService] = None,
        settings: Optional[Settings] = None,
    ):
        self._db = db
        self._embedder = embedder
        self._vector_client = vector_client
        self._analyzer = ConflictAnalyzer(generator)
        self._threshold = threshold
        self._dispatcher = dispatcher
        self._frontend_base_url = frontend_base_url
        self._sync_service = sync_service
        self._settings = settings

    async def detect_and_record(self, document_id: int, organization_id: int) -> list[ConflictDetectionResult]:
        """문서의 핵심 조항을 추출하여 같은 조직의 기존 정책들과 상충 여부를 분석하고 기록한다."""
        doc = await self._db.find_document_with_content(document_id)
        if not doc:
            log.info("문서 정보 없음 (상충 검사 생략)", document_id=document_id)
            CONFLICT_DETECTIONS.labels(outcome="skipped").inc()
            return []

        doc_title = doc["title"]
        doc_content = doc.get("content") or ""
        doc_version = doc.get("current_version")
        org_id = organization_id

        # Neo4j 지식 그래프 동기화 (노드 및 인과 관계 추출)
        if self._sync_service:
            try:
                await self._sync_service.sync_document(
                    doc_id=document_id,
                    title=doc_title,
                    content=doc_content,
                    status=str(doc.get("decision_status", "CURRENT")),
                    authored_at=str(doc.get("authored_at", "")),
                    organization_id=org_id,
                )
            except Exception as e:
                log.warning("Neo4j 지식 그래프 동기화 실패 (무시 후 상충 검사 계속)", error=str(e))

        assertions = await self._analyzer.extract_assertions(doc_content)
        if not assertions:
            log.info("문서에서 추출된 핵심 조항 없음", document_id=document_id)
            CONFLICT_DETECTIONS.labels(outcome="skipped").inc()
            return []

        log.info(
            "정책 조항 추출 완료, 기존 규정 대비 상충 탐색 시작",
            document_id=document_id,
            assertions_count=len(assertions),
            assertions=assertions,
        )

        detected_conflicts: list[ConflictDetectionResult] = []

        for assertion in assertions:
            # 색인된 문서 청크를 찾는 검색이므로 질의 taskType(RETRIEVAL_QUERY)으로 임베딩한다
            query_vector = await self._embedder.embed_query(assertion[:_EMBED_TEXT_LIMIT])
            candidate_hits = await self._vector_client.search_points(
                vector=query_vector,
                organization_id=org_id,
                exclude_doc_id=document_id,
                limit=5,
            )

            checked_doc_ids: set[int] = set()
            for hit in candidate_hits:
                if not hit.content or hit.document_id in checked_doc_ids:
                    continue
                checked_doc_ids.add(hit.document_id)

                target_doc = await self._db.find_document_with_content(hit.document_id)
                target_title = target_doc["title"] if target_doc else f"문서 #{hit.document_id}"

                result = await self._analyzer.analyze_contradiction(
                    new_doc_id=document_id,
                    new_doc_title=doc_title,
                    new_clause=assertion,
                    existing_doc_id=hit.document_id,
                    existing_doc_title=target_title,
                    existing_clause=hit.content,
                )

                if result and result.severity_score >= self._threshold:
                    detected_conflicts.append(result)
                    conflict_id = await self._db.record_conflict(
                        source_doc_id=result.source_doc_id,
                        target_doc_id=result.target_doc_id,
                        conflict_type=result.conflict_type,
                        severity_score=result.severity_score,
                        source_clause=result.source_assertion,
                        target_clause=result.target_assertion,
                        # decision_conflicts 에 권고 조치 전용 컬럼이 없어 reasoning 에 함께 담는다(알림에는 따로 실린다)
                        reasoning=f"{result.reason} (권고: {result.suggested_action})",
                        affected_teams=result.impacted_teams,
                        source_doc_version=doc_version,
                    )
                    CONFLICTS_RECORDED.inc()
                    log.warning(
                        "🚨 정책 상충(Conflict) 감지 및 DB 저장 완료",
                        conflict_id=conflict_id,
                        source_doc_id=result.source_doc_id,
                        target_doc_id=result.target_doc_id,
                        severity_score=result.severity_score,
                        conflict_type=result.conflict_type,
                        reason=result.reason,
                    )

                    # Neo4j 지식 그래프에 상충 관계 동기화
                    if self._sync_service:
                        try:
                            await self._sync_service.sync_conflict_relation(
                                source_doc_id=result.source_doc_id,
                                target_doc_id=result.target_doc_id,
                                conflict_type=result.conflict_type,
                                severity_score=result.severity_score,
                                reasoning=result.reason,
                            )
                        except Exception as e:
                            log.warning("Neo4j 상충 관계 동기화 실패", error=str(e))

                    # 실시간 웹훅/이메일 알림 발송
                    # get_active_channels()(전역 설정)만으로 게이트를 걸면 전역 채널이 전부
                    # 꺼져 있을 때 팀별 채널(extra_channels)까지 통째로 건너뛰게 된다. dispatch() 내부가 심각도·활성 채널(전역+팀) 여부를 스스로 판단해
                    # 아무 것도 없으면 조용히 no-op 하므로, 여기서는 dispatcher 존재 여부만 본다.
                    if self._dispatcher:
                        payload = ConflictNotificationPayload(
                            conflict_id=conflict_id,
                            source_doc_id=result.source_doc_id,
                            source_doc_title=doc_title,
                            source_clause=result.source_assertion,
                            target_doc_id=result.target_doc_id,
                            target_doc_title=target_title,
                            target_clause=result.target_assertion,
                            conflict_type=result.conflict_type,
                            severity_score=result.severity_score,
                            reasoning=result.reason,
                            affected_teams=result.impacted_teams,
                            recommended_action=result.suggested_action,
                            frontend_base_url=self._frontend_base_url,
                        )
                        team_channels, team_emails = await self._resolve_team_channels(
                            {doc.get("team_id"), target_doc.get("team_id") if target_doc else None}
                        )
                        author_channels = await self._resolve_author_channels(
                            [result.source_doc_id, result.target_doc_id], already_notified=team_emails
                        )
                        await self._dispatcher.dispatch(
                            payload, extra_channels=team_channels + author_channels
                        )

        # 재감지를 끝냈으니 개정 전 판을 보고 열려 있던 상충을 닫는다.
        # 조항을 추출하지 못한 경우엔 위에서 이미 반환했으므로, 여기까지 왔다면 이번 판을
        # 실제로 검사한 것이다 — LLM 이 한 번 실패했다고 멀쩡한 상충을 닫아버리지 않는다.
        if doc_version is not None:
            superseded = await self._db.supersede_outdated_conflicts(document_id, doc_version)
            if superseded:
                CONFLICTS_SUPERSEDED.inc(superseded)
                log.info(
                    "개정 전 판 기준 상충을 SUPERSEDED 로 닫음",
                    document_id=document_id,
                    current_version=doc_version,
                    count=superseded,
                )

        if not detected_conflicts:
            log.info("문서 상충 검사 완료: 특이사항 없음", document_id=document_id)
        CONFLICT_DETECTIONS.labels(outcome="conflicts_found" if detected_conflicts else "none").inc()

        return detected_conflicts

    async def _resolve_author_channels(
        self, document_ids: list[int], already_notified: set[str]
    ) -> list[NotificationChannel]:
        """상충에 걸린 문서의 작성자에게 보내는 개인 알림.

        already_notified 에 있는 주소는 뺀다 — 작성자가 그 팀 소속이면 팀 채널에서 이미
        같은 메일을 받는다. 그대로 두면 한 상충에 같은 사람이 두 통을 받는다.

        심각도 게이트는 팀 알림과 같다(dispatcher 의 min_severity). 작성자라고 더 낮은
        임계치를 적용하지는 않았다 — 그건 별도 제품 판단이라 임의로 정하지 않는다.
        """
        if self._settings is None:
            return []

        emails = [e for e in await self._db.find_document_author_emails(document_ids)
                  if e not in already_notified]
        if not emails:
            return []

        log.info("문서 작성자에게 개인 알림 발송", document_ids=document_ids, recipients=len(emails))
        return [EmailNotificationChannel(
            smtp_host=self._settings.smtp_host,
            smtp_port=self._settings.smtp_port,
            smtp_username=self._settings.smtp_username,
            smtp_password=self._settings.smtp_password,
            smtp_use_tls=self._settings.smtp_use_tls,
            email_from=self._settings.email_from,
            email_to=",".join(emails),
            enabled=True,
        )]

    async def _resolve_team_channels(
        self, team_ids: set[int | None]
    ) -> tuple[list[NotificationChannel], set[str]]:
        """상충 문서(source/target)의 team_id 로 팀별 알림 채널을 만든다.

        team_notification_channel 행이 아예 없는 팀은 건드리지 않는다 — 전역 설정 채널로만
        가는 기존 동작을 그대로 유지한다(팀이 한 번도 이 설정을 저장한 적 없으면 "opt-in" 전).
        행은 있는데 notification_email 이 비어 있으면 그때만 팀원 이메일로 폴백한다.

        두 번째 반환값은 이 호출이 메일을 보내게 되는 주소 집합이다. 작성자 개인 알림이
        같은 사람에게 두 번 보내지 않도록 빼는 데 쓴다(_resolve_author_channels).
        """
        ids = [t for t in team_ids if t is not None]
        if not ids:
            return [], set()

        configs = await self._db.find_team_channels(ids)
        channels: list[NotificationChannel] = []
        notified_emails: set[str] = set()
        for team_id in ids:
            config = configs.get(team_id)
            if not config:
                continue

            if config.get("slack_webhook_url"):
                channels.append(SlackNotificationChannel(webhook_url=config["slack_webhook_url"], enabled=True))
            if config.get("teams_webhook_url"):
                channels.append(TeamsNotificationChannel(webhook_url=config["teams_webhook_url"], enabled=True))

            raw_emails = config.get("notification_email")
            emails = [e.strip() for e in raw_emails.split(",") if e.strip()] if raw_emails else []
            if not emails:
                emails = await self._db.find_team_member_emails(team_id)
            if emails and self._settings is not None:
                channels.append(EmailNotificationChannel(
                    smtp_host=self._settings.smtp_host,
                    smtp_port=self._settings.smtp_port,
                    smtp_username=self._settings.smtp_username,
                    smtp_password=self._settings.smtp_password,
                    smtp_use_tls=self._settings.smtp_use_tls,
                    email_from=self._settings.email_from,
                    email_to=",".join(emails),
                    enabled=True,
                ))
                notified_emails.update(emails)
        return channels, notified_emails

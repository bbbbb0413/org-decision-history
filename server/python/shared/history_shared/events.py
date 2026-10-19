"""Redis Stream 이벤트 모델.

규약: contracts/events/document-events.schema.json, contracts/events/index-events.schema.json.
항목은 필드 3개(aggregateId, eventType, payload)이고 payload 는 JSON 문자열이다.
"""
import json
from collections.abc import Mapping
from dataclasses import dataclass

# 스트림 이름은 발행자(document-service Java)와 따로 적혀 있다. 계약 파일의 x-stream 과 같은지
# 양쪽 테스트가 확인한다(shared tests/test_events.py, document-service OutboxPublisherContractTest)
STREAM_DOCUMENT_EVENTS = "stream:document-events"
STREAM_INDEX_EVENTS = "stream:index-events"

# PERMISSION_CHANGED 는 DB enum(document.outbox_event_type)에만 남아 있다. 발행하는 곳이 없어 계약에서 뺐다 —
# 문서의 조직·팀이 바뀌는 경로는 수정(DOCUMENT_UPDATED)이고 그 재색인이 scope_key 를 갱신한다
DOCUMENT_EVENT_TYPES = frozenset({"DOCUMENT_CREATED", "DOCUMENT_UPDATED", "DOCUMENT_DELETED"})
DOCUMENT_INDEXED = "DOCUMENT_INDEXED"
DOCUMENT_UNINDEXED = "DOCUMENT_UNINDEXED"


class EventParseError(ValueError):
    """계약과 다른 스트림 항목. 다시 읽어도 결과가 같다."""


def _payload(fields: Mapping[str, str]) -> dict:
    try:
        payload = json.loads(fields["payload"])
    except (KeyError, TypeError, ValueError) as e:
        raise EventParseError(f"payload 를 JSON 으로 읽을 수 없다: {e}") from None
    if not isinstance(payload, dict):
        raise EventParseError("payload 가 JSON 객체가 아니다")
    return payload


def _team_id(value) -> int | None:
    # document-service 는 팀이 없으면 빈 문자열을 보낸다(Map.of 가 null 을 받지 않아 생긴 관례)
    return None if value in (None, "") else int(value)


def _event_id(value) -> int | None:
    """아웃박스 행 번호. 선택 필드다. 값이 이상하면 없는 것으로 본다 — 접수 키가
    이벤트 ID 없는 형식이 될 뿐이고, 여기서 예외를 던지면 이벤트 하나가 DLQ 로 빠진다."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DocumentEvent:
    """stream:document-events 항목."""

    event_type: str
    document_id: int
    version: int
    organization_id: int
    team_id: int | None
    document_type: str
    # 아웃박스 행 번호. 접수 키를 이 값까지 포함해 만들면 같은 판에 파일을 다시 올려도
    # 재색인이 건너뛰어지지 않는다(재전달에는 같은 값이 오므로 멱등은 유지된다).
    # 선택 필드라 없을 수 있다 — 그때 소비자는 문서·판·종류만으로 키를 만든다.
    event_id: int | None = None

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "DocumentEvent":
        event_type = fields.get("eventType")
        if event_type not in DOCUMENT_EVENT_TYPES:
            raise EventParseError(f"알 수 없는 eventType: {event_type!r}")
        payload = _payload(fields)
        try:
            return cls(
                event_type=event_type,
                document_id=int(payload["documentId"]),
                version=int(payload["version"]),
                organization_id=int(payload["organizationId"]),
                team_id=_team_id(payload.get("teamId")),
                document_type=str(payload["documentType"]),
                event_id=_event_id(fields.get("eventId")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise EventParseError(f"payload 필드가 계약과 다르다: {e}") from None


@dataclass(frozen=True)
class DocumentIndexedEvent:
    """stream:index-events 의 DOCUMENT_INDEXED."""

    document_id: int
    version: int
    chunk_count: int
    organization_id: int
    team_id: int | None

    def to_fields(self) -> dict[str, str]:
        return {
            "aggregateId": str(self.document_id),
            "eventType": DOCUMENT_INDEXED,
            "payload": json.dumps(
                {
                    "documentId": self.document_id,
                    "version": self.version,
                    "chunkCount": self.chunk_count,
                    "organizationId": self.organization_id,
                    "teamId": self.team_id,
                }
            ),
        }

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "DocumentIndexedEvent":
        if fields.get("eventType") != DOCUMENT_INDEXED:
            raise EventParseError(f"DOCUMENT_INDEXED 가 아니다: {fields.get('eventType')!r}")
        payload = _payload(fields)
        try:
            return cls(
                document_id=int(payload["documentId"]),
                version=int(payload["version"]),
                chunk_count=int(payload["chunkCount"]),
                organization_id=int(payload["organizationId"]),
                team_id=_team_id(payload.get("teamId")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise EventParseError(f"payload 필드가 계약과 다르다: {e}") from None


@dataclass(frozen=True)
class DocumentUnindexedEvent:
    """stream:index-events 의 DOCUMENT_UNINDEXED. 삭제된 문서를 색인에서 걷어낸 뒤 발행한다.

    governance-worker 가 이 문서를 지식 그래프에서 빼는 데 쓴다. version 은 삭제 시점의 판이다.
    """

    document_id: int
    version: int
    organization_id: int
    team_id: int | None

    def to_fields(self) -> dict[str, str]:
        return {
            "aggregateId": str(self.document_id),
            "eventType": DOCUMENT_UNINDEXED,
            "payload": json.dumps(
                {
                    "documentId": self.document_id,
                    "version": self.version,
                    "organizationId": self.organization_id,
                    "teamId": self.team_id,
                }
            ),
        }

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "DocumentUnindexedEvent":
        if fields.get("eventType") != DOCUMENT_UNINDEXED:
            raise EventParseError(f"DOCUMENT_UNINDEXED 가 아니다: {fields.get('eventType')!r}")
        payload = _payload(fields)
        try:
            return cls(
                document_id=int(payload["documentId"]),
                version=int(payload["version"]),
                organization_id=int(payload["organizationId"]),
                team_id=_team_id(payload.get("teamId")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise EventParseError(f"payload 필드가 계약과 다르다: {e}") from None


IndexEvent = DocumentIndexedEvent | DocumentUnindexedEvent


def parse_index_event(fields: Mapping[str, str]) -> IndexEvent:
    """stream:index-events 항목을 종류에 맞는 이벤트로 읽는다. 모르는 종류는 EventParseError."""
    event_type = fields.get("eventType")
    if event_type == DOCUMENT_INDEXED:
        return DocumentIndexedEvent.from_fields(fields)
    if event_type == DOCUMENT_UNINDEXED:
        return DocumentUnindexedEvent.from_fields(fields)
    raise EventParseError(f"알 수 없는 eventType: {event_type!r}")

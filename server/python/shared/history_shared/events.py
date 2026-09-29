"""Redis Stream 이벤트 모델.

규약: contracts/events/document-events.schema.json, contracts/events/index-events.schema.json.
항목은 필드 3개(aggregateId, eventType, payload)이고 payload 는 JSON 문자열이다.
"""
import json
from collections.abc import Mapping
from dataclasses import dataclass

STREAM_DOCUMENT_EVENTS = "stream:document-events"
STREAM_INDEX_EVENTS = "stream:index-events"

DOCUMENT_EVENT_TYPES = frozenset({"DOCUMENT_CREATED", "DOCUMENT_UPDATED", "DOCUMENT_DELETED", "PERMISSION_CHANGED"})
DOCUMENT_INDEXED = "DOCUMENT_INDEXED"


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

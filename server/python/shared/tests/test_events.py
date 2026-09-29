import json

import pytest
from jsonschema import Draft202012Validator

from history_shared.events import DocumentEvent, DocumentIndexedEvent, EventParseError


def validate_entry(schema: dict, defs_key: str, fields: dict) -> list[str]:
    """스트림 항목과 그 안의 payload JSON 을 함께 검증한다(contentSchema 는 기본 검증기가 보지 않는다)."""
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(fields)]
    payload_schema = {**schema["$defs"][defs_key], "$defs": schema["$defs"]}
    errors += [e.message for e in Draft202012Validator(payload_schema).iter_errors(json.loads(fields["payload"]))]
    return errors


# document-service OutboxPublisher 가 실제로 발행한 항목 모양(jsonb 를 거쳐 키 순서가 바뀐다)
SAMPLE_WITHOUT_TEAM = {
    "aggregateId": "22",
    "eventType": "DOCUMENT_CREATED",
    "payload": '{"teamId": "", "version": 1, "documentId": 22, "documentType": "POLICY_DOC", "organizationId": 1}',
}
SAMPLE_WITH_TEAM = {
    "aggregateId": "19",
    "eventType": "DOCUMENT_UPDATED",
    "payload": '{"teamId": 1, "version": 3, "documentId": 19, "documentType": "POLICY_DOC", "organizationId": 1}',
}


def test_samples_match_document_events_schema(contracts_dir, load_json):
    schema = load_json(contracts_dir / "events" / "document-events.schema.json")
    for sample in (SAMPLE_WITHOUT_TEAM, SAMPLE_WITH_TEAM):
        assert validate_entry(schema, "DocumentEventPayload", sample) == []


def test_document_event_parsing():
    event = DocumentEvent.from_fields(SAMPLE_WITHOUT_TEAM)
    assert (event.event_type, event.document_id, event.version, event.organization_id) == ("DOCUMENT_CREATED", 22, 1, 1)
    assert event.team_id is None
    assert event.document_type == "POLICY_DOC"
    assert DocumentEvent.from_fields(SAMPLE_WITH_TEAM).team_id == 1


@pytest.mark.parametrize(
    "fields",
    [
        {**SAMPLE_WITH_TEAM, "eventType": "SOMETHING_ELSE"},
        {**SAMPLE_WITH_TEAM, "payload": "not json"},
        {**SAMPLE_WITH_TEAM, "payload": "[1, 2]"},
        {**SAMPLE_WITH_TEAM, "payload": '{"documentId": 1}'},
        {"eventType": "DOCUMENT_CREATED"},
    ],
)
def test_contract_violations_raise_parse_error(fields):
    with pytest.raises(EventParseError):
        DocumentEvent.from_fields(fields)


def test_document_indexed_event_matches_draft_schema(contracts_dir, load_json):
    schema = load_json(contracts_dir / "events" / "index-events.schema.json")
    for team_id in (None, 3):
        event = DocumentIndexedEvent(document_id=22, version=1, chunk_count=4, organization_id=1, team_id=team_id)
        fields = event.to_fields()
        assert validate_entry(schema, "DocumentIndexedPayload", fields) == []
        assert DocumentIndexedEvent.from_fields(fields) == event

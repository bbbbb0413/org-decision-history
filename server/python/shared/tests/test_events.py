import json

import pytest
from jsonschema import Draft202012Validator

from history_shared.events import (
    STREAM_DOCUMENT_EVENTS,
    STREAM_INDEX_EVENTS,
    DocumentEvent,
    DocumentIndexedEvent,
    DocumentUnindexedEvent,
    EventParseError,
    parse_index_event,
)


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
        # 발행하는 곳이 없어 계약에서 뺐다. 들어오면 계약 위반으로 DLQ 에 간다
        {**SAMPLE_WITH_TEAM, "eventType": "PERMISSION_CHANGED"},
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


def test_document_unindexed_event_matches_schema(contracts_dir, load_json):
    schema = load_json(contracts_dir / "events" / "index-events.schema.json")
    for team_id in (None, 3):
        event = DocumentUnindexedEvent(document_id=22, version=4, organization_id=1, team_id=team_id)
        fields = event.to_fields()
        assert validate_entry(schema, "DocumentUnindexedPayload", fields) == []
        assert parse_index_event(fields) == event


def test_parse_index_event_dispatches_by_type():
    indexed = DocumentIndexedEvent(document_id=1, version=1, chunk_count=2, organization_id=1, team_id=None)
    assert parse_index_event(indexed.to_fields()) == indexed
    with pytest.raises(EventParseError):
        parse_index_event({**indexed.to_fields(), "eventType": "DOCUMENT_CREATED"})


def test_event_types_match_contract_enums(contracts_dir, load_json):
    from history_shared.events import DOCUMENT_EVENT_TYPES, DOCUMENT_INDEXED, DOCUMENT_UNINDEXED

    document = load_json(contracts_dir / "events" / "document-events.schema.json")
    index = load_json(contracts_dir / "events" / "index-events.schema.json")
    assert DOCUMENT_EVENT_TYPES == set(document["properties"]["eventType"]["enum"])
    assert {DOCUMENT_INDEXED, DOCUMENT_UNINDEXED} == set(index["properties"]["eventType"]["enum"])


def test_stream_names_match_contracts(contracts_dir, load_json):
    """스트림 이름은 Java(document-service)와 Python 에 따로 적혀 있다. 한쪽만 바꾸면 오류 없이 전달이 끊기므로
    양쪽 모두 계약 파일의 x-stream 과 같은지 확인한다(Java 쪽은 OutboxPublisherContractTest)."""
    assert STREAM_DOCUMENT_EVENTS == load_json(contracts_dir / "events" / "document-events.schema.json")["x-stream"]
    assert STREAM_INDEX_EVENTS == load_json(contracts_dir / "events" / "index-events.schema.json")["x-stream"]

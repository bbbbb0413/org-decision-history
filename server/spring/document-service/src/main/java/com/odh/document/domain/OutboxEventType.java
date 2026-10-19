package com.odh.document.domain;

/**
 * 문서 변경 이벤트 종류. 값은 contracts/events/document-events.schema.json 의 eventType 과 같다.
 *
 * <p>DB enum(outbox_event_type)에는 PERMISSION_CHANGED 가 더 있지만 발행하는 곳이 없다 —
 * 문서의 조직·팀이 바뀌는 경로는 수정(DOCUMENT_UPDATED)이고 그 재색인이 권한 범위(scope_key)를 갱신한다.
 * PostgreSQL 은 enum 값을 지울 수 없어 DB 에는 남아 있다.
 */
public enum OutboxEventType {
    DOCUMENT_CREATED,
    DOCUMENT_UPDATED,
    DOCUMENT_DELETED
}

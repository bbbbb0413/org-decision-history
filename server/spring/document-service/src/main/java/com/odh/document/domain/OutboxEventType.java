package com.odh.document.domain;

/** DB 의 outbox_event_type enum 과 값이 같다. 문서 변경 이벤트 종류 */
public enum OutboxEventType {
    DOCUMENT_CREATED,
    DOCUMENT_UPDATED,
    DOCUMENT_DELETED,
    PERMISSION_CHANGED
}

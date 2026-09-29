package com.odh.chat.timeline;

import java.util.List;

/** 타임라인 요청·응답 타입. */
public final class TimelineDtos {

    public record Event(
            Long documentId,
            String documentTitle,
            String documentType,
            String occurredAt,
            String decisionStatus,
            String projectName,
            String decisionExcerpt,
            Long fileId
    ) {
    }

    public record Conflict(
            Long documentAId,
            String documentATitle,
            String documentAAuthoredAt,
            String documentAStatus,
            Long documentBId,
            String documentBTitle,
            String documentBAuthoredAt,
            String documentBStatus,
            String difference
    ) {
    }

    public record Response(String topic, List<Event> events, List<Conflict> conflicts) {
    }

    private TimelineDtos() {
    }
}

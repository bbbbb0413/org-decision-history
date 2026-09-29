package com.odh.document.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.odh.document.domain.Document;
import com.odh.document.domain.OutboxEventType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;

/**
 * 문서 변경 이벤트 발행.
 *
 * 이벤트를 문서 저장과 같은 트랜잭션에서 아웃박스 테이블에 남기고,
 * 발행기가 주기적으로 Redis Stream 으로 보낸다.
 * 저장과 발행을 한 트랜잭션에 묶을 수 없기 때문에 나온 구조다.
 * 저장 직후 발행하다 실패하면 이벤트가 사라지고, 발행 먼저 하면 저장 실패 시 유령 이벤트가 남는다.
 */
@Component
public class OutboxPublisher {

    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);
    private static final String STREAM_KEY = "stream:document-events";
    private static final int BATCH = 100;

    private final JdbcClient jdbc;
    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;

    public OutboxPublisher(JdbcClient jdbc, StringRedisTemplate redis, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.redis = redis;
        this.mapper = mapper;
    }

    /** 문서 저장과 같은 트랜잭션에서 호출한다. */
    public void record(Document document, OutboxEventType type) {
        try {
            String payload = mapper.writeValueAsString(Map.of(
                    "documentId", document.getId(),
                    "version", document.getCurrentVersion(),
                    "organizationId", document.getOrganizationId(),
                    "teamId", document.getTeamId() == null ? "" : document.getTeamId(),
                    "documentType", document.getDocumentType().name()));

            jdbc.sql("""
                            INSERT INTO document_outbox (aggregate_id, event_type, payload)
                            VALUES (:aggregateId, CAST(:eventType AS outbox_event_type), CAST(:payload AS jsonb))
                            """)
                    .param("aggregateId", document.getId())
                    .param("eventType", type.name())
                    .param("payload", payload)
                    .update();
        } catch (Exception e) {
            throw new IllegalStateException("아웃박스 기록에 실패했다", e);
        }
    }

    /** 미발행 이벤트를 Redis Stream 으로 보낸다. 4단계 색인 워커가 이 스트림을 소비한다. */
    @Scheduled(fixedDelay = 2000)
    @Transactional
    public void publishPending() {
        List<PendingEvent> rows = jdbc.sql("""
                        SELECT id, aggregate_id, event_type::text AS event_type, payload::text AS payload
                        FROM document_outbox
                        WHERE published_at IS NULL
                        ORDER BY occurred_at
                        LIMIT :batch
                        FOR UPDATE SKIP LOCKED
                        """)
                .param("batch", BATCH)
                .query((rs, i) -> new PendingEvent(
                        rs.getLong("id"),
                        String.valueOf(rs.getLong("aggregate_id")),
                        rs.getString("event_type"),
                        rs.getString("payload")))
                .list();

        if (rows.isEmpty()) {
            return;
        }
        for (PendingEvent event : rows) {
            // eventId 는 아웃박스 행 번호다. 소비자가 접수 키를 만들 때 쓴다 —
            // (문서, 판, 종류)만으로는 같은 판에 파일을 다시 올렸을 때 키가 겹쳐서
            // 두 번째 재색인이 조용히 건너뛰어졌다. 행 번호는 재전달에도 고정이므로
            // 멱등은 그대로 유지된다.
            redis.opsForStream().add(STREAM_KEY, Map.of(
                    "eventId", String.valueOf(event.id()),
                    "aggregateId", event.aggregateId(),
                    "eventType", event.eventType(),
                    "payload", event.payload()));
            jdbc.sql("UPDATE document_outbox SET published_at = now() WHERE id = :id")
                    .param("id", event.id()).update();
        }
        log.debug("문서 변경 이벤트 {}건 발행", rows.size());
    }

    private record PendingEvent(long id, String aggregateId, String eventType, String payload) {
    }
}

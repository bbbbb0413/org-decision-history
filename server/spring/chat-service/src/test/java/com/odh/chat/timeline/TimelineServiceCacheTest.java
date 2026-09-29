package com.odh.chat.timeline;

import com.odh.common.PermissionScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 타임라인 상충 캐시가 "아직 판정한 적 없음"과 "판정했는데 0건"을 구분하는지 검증한다.
 *
 * TimelineService.findByTopic은 document.document·indexing.document_chunk를 직접
 * 읽는다(document-service·index-worker 소유, chat-service의 Flyway가 관리하지 않는다).
 * 실제 스키마를 그대로 재현할 필요는 없어서, 이 쿼리가 돌 수 있을 만큼만 최소 스텁
 * 테이블을 수동으로 만든다.
 */
@SpringBootTest
@Testcontainers
class TimelineServiceCacheTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    private static final PermissionScope SYSTEM_ADMIN =
            new PermissionScope(Set.of(), Set.of(), Set.of(), Set.of(), true);

    @Autowired
    private TimelineService service;

    @Autowired
    private JdbcClient jdbc;

    @BeforeEach
    void setUp() {
        jdbc.sql("CREATE SCHEMA IF NOT EXISTS document").update();
        jdbc.sql("CREATE SCHEMA IF NOT EXISTS indexing").update();
        jdbc.sql("""
                CREATE TABLE IF NOT EXISTS document.document (
                    id               BIGINT PRIMARY KEY,
                    title            VARCHAR(200) NOT NULL,
                    content          TEXT NOT NULL,
                    document_type    TEXT NOT NULL,
                    decision_status  TEXT NOT NULL,
                    organization_id  BIGINT NOT NULL,
                    team_id          BIGINT,
                    project_name     VARCHAR(100),
                    current_version  INT NOT NULL DEFAULT 1,
                    authored_at      DATE NOT NULL,
                    deleted_at       TIMESTAMPTZ
                )
                """).update();
        jdbc.sql("""
                CREATE TABLE IF NOT EXISTS indexing.document_chunk (
                    document_id      BIGINT NOT NULL,
                    document_version INT NOT NULL,
                    chunk_tag        TEXT,
                    chunk_no         INT,
                    content          TEXT
                )
                """).update();
        jdbc.sql("""
                CREATE TABLE IF NOT EXISTS document.document_file (
                    id           BIGINT,
                    document_id  BIGINT NOT NULL,
                    version      INT NOT NULL
                )
                """).update();

        jdbc.sql("TRUNCATE chat.timeline_conflict RESTART IDENTITY").update();
        jdbc.sql("DELETE FROM document.document").update();
    }

    @Test
    void topicWithNoMatchingDocuments_cachesZeroConflictResult() {
        String topic = "존재하지-않는-주제-" + System.nanoTime();

        TimelineDtos.Response first = service.timelineOf(topic, SYSTEM_ADMIN);
        assertThat(first.conflicts()).isEmpty();
        assertThat(cachedRowCount(topic)).isEqualTo(1); // sentinel 1건

        // 다시 조회해도 재판정(재기록)하지 않는다 — 캐시가 있었다면 row 수는 그대로여야 한다
        TimelineDtos.Response second = service.timelineOf(topic, SYSTEM_ADMIN);
        assertThat(second.conflicts()).isEmpty();
        assertThat(cachedRowCount(topic)).isEqualTo(1);
    }

    @Test
    void topicWithDocumentsButNoConflict_alsoCachesZeroConflictResult() {
        // 결정 대목(decision_excerpt)이 없는 문서끼리는 ConflictDetector가 애초에 비교하지
        // 않는다 — 상충 0건이 되는 가장 흔한 실제 경로다
        String topic = "일반-공지-" + System.nanoTime();
        insertDocument(1001L, topic + " 1차", 1L);
        insertDocument(1002L, topic + " 2차", 1L);

        TimelineDtos.Response first = service.timelineOf(topic, SYSTEM_ADMIN);
        assertThat(first.events()).hasSize(2);
        assertThat(first.conflicts()).isEmpty();
        assertThat(cachedRowCount(topic)).isEqualTo(1);

        TimelineDtos.Response second = service.timelineOf(topic, SYSTEM_ADMIN);
        assertThat(second.conflicts()).isEmpty();
        assertThat(cachedRowCount(topic)).isEqualTo(1);
    }

    private void insertDocument(long id, String title, long orgId) {
        jdbc.sql("""
                        INSERT INTO document.document
                            (id, title, content, document_type, decision_status, organization_id, authored_at)
                        VALUES (:id, :title, 'content', 'POLICY_DOC', 'CURRENT', :orgId, CURRENT_DATE)
                        """)
                .param("id", id).param("title", title).param("orgId", orgId)
                .update();
    }

    private long cachedRowCount(String topic) {
        String normalized = topic.toLowerCase().replaceAll("\\s+", " ").strip();
        return jdbc.sql("SELECT count(*) FROM chat.timeline_conflict WHERE topic_normalized = :topic")
                .param("topic", normalized)
                .query(Long.class).single();
    }
}

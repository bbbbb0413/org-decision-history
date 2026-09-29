package com.odh.auth.ops;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 스트림 DLQ 조회·재처리.
 *
 * 실제 Redis 로 돌린다 — XADD/XDEL 을 MULTI 로 묶는 부분과 스트림 id 파싱이 핵심이라
 * 목으로는 의미 있는 검증이 안 된다.
 */
@SpringBootTest
@Testcontainers
class OpsDlqServiceTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
    }

    private static final OpsDlqService.Target TARGET = OpsDlqService.Target.INDEX_EVENTS;

    @Autowired
    private OpsDlqService dlq;

    @Autowired
    private StringRedisTemplate redisTemplate;

    @BeforeEach
    void clean() {
        for (OpsDlqService.Target t : OpsDlqService.Target.values()) {
            redisTemplate.delete(t.stream());
            redisTemplate.delete(t.dlqStream());
        }
    }

    /** 컨슈머가 남기는 것과 같은 모양의 DLQ 항목을 만든다. */
    private String putDlqEntry(OpsDlqService.Target target, String documentId, String reason) {
        Map<String, String> fields = Map.of(
                "eventType", "DOCUMENT_INDEXED",
                "documentId", documentId,
                "organizationId", "1",
                "dlq.sourceStream", target.stream(),
                "dlq.sourceId", "1726900000000-0",
                "dlq.group", "governance-worker",
                "dlq.deliveries", "6",
                "dlq.reason", reason);
        var id = redisTemplate.opsForStream()
                .add(StreamRecords.mapBacked(fields).withStreamKey(target.dlqStream()));
        return id == null ? null : id.getValue();
    }

    private List<MapRecord<String, Object, Object>> read(String stream) {
        List<MapRecord<String, Object, Object>> records =
                redisTemplate.opsForStream().range(stream, Range.unbounded());
        return records == null ? List.of() : records;
    }

    @Test
    void 비어_있으면_건수는_0_이고_가장_오래된_시각은_없다() {
        List<OpsDtos.DlqStatus> statuses = dlq.statuses();

        assertThat(statuses).hasSize(OpsDlqService.Target.values().length);
        assertThat(statuses).allSatisfy(s -> {
            assertThat(s.count()).isZero();
            assertThat(s.oldestAt()).isNull();
        });
    }

    @Test
    void 쌓인_항목_수와_가장_오래된_시각을_알려준다() {
        putDlqEntry(TARGET, "11", "전달 6회로 한도 5회를 넘었다");
        putDlqEntry(TARGET, "12", "처리할 수 없는 항목");

        OpsDtos.DlqStatus status = dlq.statuses().stream()
                .filter(s -> s.key().equals(TARGET.key())).findFirst().orElseThrow();

        assertThat(status.count()).isEqualTo(2);
        assertThat(status.oldestAt()).isNotNull();
        assertThat(status.consumerService()).isEqualTo("governance-worker");
        assertThat(status.dlqStream()).isEqualTo("stream:index-events:dlq");
    }

    @Test
    void 항목은_최근_것부터_내려가고_메타와_본문이_나뉜다() {
        putDlqEntry(TARGET, "11", "첫 실패");
        putDlqEntry(TARGET, "12", "두 번째 실패");

        List<OpsDtos.DlqEntry> entries = dlq.entries(TARGET, 10);

        assertThat(entries).hasSize(2);
        OpsDtos.DlqEntry newest = entries.get(0);
        assertThat(newest.reason()).isEqualTo("두 번째 실패");
        assertThat(newest.deliveries()).isEqualTo(6);
        assertThat(newest.consumerGroup()).isEqualTo("governance-worker");
        assertThat(newest.addedAt()).isNotNull();
        // dlq.* 메타는 본문에서 빠진다 — 되돌릴 때 실제로 넣는 필드가 무엇인지 화면에서 보여야 한다
        assertThat(newest.payload()).containsOnlyKeys("eventType", "documentId", "organizationId");
        assertThat(newest.payload()).containsEntry("documentId", "12");
    }

    @Test
    void 재처리하면_메타를_뗀_본문이_원본_스트림으로_돌아가고_DLQ_에서_빠진다() {
        String id = putDlqEntry(TARGET, "11", "전달 한도 초과");

        OpsDtos.DlqActionResult result = dlq.replay(TARGET, List.of(id));

        assertThat(result.succeeded()).isEqualTo(1);
        assertThat(result.failedIds()).isEmpty();
        assertThat(read(TARGET.dlqStream())).isEmpty();

        List<MapRecord<String, Object, Object>> restored = read(TARGET.stream());
        assertThat(restored).hasSize(1);
        Map<Object, Object> fields = restored.get(0).getValue();
        assertThat(fields).containsEntry("documentId", "11").containsEntry("eventType", "DOCUMENT_INDEXED");
        // 되돌린 항목은 컨슈머가 처음 받는 것과 같아야 한다 — dlq.* 가 섞여 들어가면 계약 위반이다
        assertThat(fields.keySet()).noneSatisfy(k -> assertThat(String.valueOf(k)).startsWith("dlq."));
    }

    @Test
    void 없는_id_는_실패로_보고하고_나머지는_계속_처리한다() {
        String id = putDlqEntry(TARGET, "11", "전달 한도 초과");

        OpsDtos.DlqActionResult result = dlq.replay(TARGET, List.of("1-1", id));

        assertThat(result.succeeded()).isEqualTo(1);
        assertThat(result.failedIds()).containsExactly("1-1");
        assertThat(read(TARGET.stream())).hasSize(1);
    }

    @Test
    void 폐기하면_원본_스트림에_넣지_않고_DLQ_에서만_지운다() {
        String id = putDlqEntry(TARGET, "11", "되살릴 수 없는 항목");

        OpsDtos.DlqActionResult result = dlq.discard(TARGET, List.of(id));

        assertThat(result.succeeded()).isEqualTo(1);
        assertThat(read(TARGET.dlqStream())).isEmpty();
        assertThat(read(TARGET.stream())).isEmpty();
    }

    @Test
    void 원본_스트림이_허용_목록_밖이면_되돌리지_않는다() {
        // 워커만 쓰는 필드지만, 조작된 값으로 임의 키에 XADD 되는 경로가 없어야 한다
        var id = redisTemplate.opsForStream().add(StreamRecords.mapBacked(Map.of(
                "eventType", "DOCUMENT_INDEXED",
                "dlq.sourceStream", "stream:공격자가-고른-키",
                "dlq.reason", "조작된 항목")).withStreamKey(TARGET.dlqStream()));

        OpsDtos.DlqActionResult result = dlq.replay(TARGET, List.of(id.getValue()));

        assertThat(result.succeeded()).isZero();
        assertThat(result.failedIds()).containsExactly(id.getValue());
        assertThat(redisTemplate.hasKey("stream:공격자가-고른-키")).isFalse();
        // 되돌리지 못한 항목은 DLQ 에 그대로 남아야 한다 — 조용히 사라지면 안 된다
        assertThat(read(TARGET.dlqStream())).hasSize(1);
    }

    @Test
    void 한_번에_처리할_수_있는_건수에_상한이_있다() {
        for (int i = 0; i < OpsDlqService.MAX_BATCH + 5; i++) {
            putDlqEntry(TARGET, String.valueOf(i), "실패");
        }
        List<String> ids = dlq.entries(TARGET, 100).stream().map(OpsDtos.DlqEntry::id).toList();

        OpsDtos.DlqActionResult result = dlq.discard(TARGET, ids);

        assertThat(result.succeeded()).isEqualTo(OpsDlqService.MAX_BATCH);
        assertThat(read(TARGET.dlqStream())).hasSize(5);
    }

    @Test
    void 허용_목록에_없는_스트림_키는_해석되지_않는다() {
        assertThat(OpsDlqService.Target.ofKey("document-events")).isPresent();
        assertThat(OpsDlqService.Target.ofKey("chat:answer:stream:x")).isEmpty();
        assertThat(OpsDlqService.Target.ofKey("../../auth:revoked-before:1")).isEmpty();
    }
}

package com.odh.auth.ops;

import jakarta.validation.constraints.NotEmpty;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/** 운영 현황 요청·응답 타입. */
public final class OpsDtos {

    public enum HealthStatus { UP, DEGRADED, DOWN, UNKNOWN }

    /** @param latencyMs UNKNOWN 이면 null 이다 */
    public record ComponentHealth(String name, HealthStatus status, Long latencyMs) {
        public static ComponentHealth unknown(String name) {
            return new ComponentHealth(name, HealthStatus.UNKNOWN, null);
        }
    }

    public record OpsHealth(List<ComponentHealth> storages, List<ComponentHealth> services, Instant checkedAt) {
    }

    public record OpsIndexStatus(int pendingCount, int runningCount, int failCount, long indexLagSeconds) {
    }

    public record OpsError(Instant occurredAt, String service, String message, String correlationId) {
    }

    public record TraceSpan(String service, String operation, Instant startedAt, long durationMs,
                            String status, String message) {
    }

    // ---- 스트림 DLQ ----

    /**
     * 스트림 하나의 DLQ 현황.
     *
     * @param key             API 에서 스트림을 가리키는 짧은 키(`document-events`)
     * @param consumerService 이 스트림을 소비하다 실패한 서비스
     * @param oldestAt        가장 오래된 항목 시각. 비어 있으면 null
     */
    public record DlqStatus(String key, String stream, String dlqStream, String consumerService,
                            long count, Instant oldestAt) {
    }

    /**
     * DLQ 항목 하나.
     *
     * @param id         DLQ 스트림에서의 항목 id. 재처리·폐기 요청에 그대로 쓴다
     * @param sourceId   원본 스트림에서의 항목 id(이미 ACK 돼 원본에는 남아 있지 않을 수 있다)
     * @param payload    {@code dlq.*} 메타를 뺀 원래 이벤트 본문
     */
    public record DlqEntry(String id, String streamKey, String sourceStream, String sourceId,
                           String consumerGroup, int deliveries, String reason, Instant addedAt,
                           Map<String, String> payload) {
    }

    /** 재처리·폐기 결과. 부분 실패를 감추지 않는다. */
    public record DlqActionResult(int succeeded, List<String> failedIds) {
    }

    /** 재처리·폐기 요청 본문. */
    public record DlqActionRequest(@NotEmpty(message = "처리할 항목 id 는 최소 1건 필요합니다") List<String> ids) {
    }

    private OpsDtos() {
    }
}

package com.odh.auth.ops;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * 운영 현황이 상태를 물어볼 다른 서비스 주소와 운영 화면 튜닝값.
 * auth-service 자신은 이 목록에 없다 — 응답하고 있다는 사실 자체가 자기 상태다.
 *
 * @param healthTimeout 다른 서비스 상태 확인의 연결·읽기 타임아웃. 환경마다 네트워크 지연이 다르다
 * @param dlqMaxEntries DLQ 목록을 한 번에 읽어올 최대 항목 수. 화면은 최근 것만 보면 되고, 큰 DLQ 를 통째로 끌어오면 안 된다
 */
@ConfigurationProperties(prefix = "ops")
public record OpsProperties(
        String gatewayUrl,
        String documentUrl,
        String indexUrl,
        String chatUrl,
        @DefaultValue("2s") Duration healthTimeout,
        @DefaultValue("100") int dlqMaxEntries
) {
    public OpsProperties {
        if (healthTimeout == null || healthTimeout.isZero() || healthTimeout.isNegative()) {
            throw new IllegalArgumentException("ops.health-timeout 은 0 보다 커야 한다: " + healthTimeout);
        }
        if (dlqMaxEntries < 1) {
            throw new IllegalArgumentException("ops.dlq-max-entries 는 1 이상이어야 한다: " + dlqMaxEntries);
        }
    }
}

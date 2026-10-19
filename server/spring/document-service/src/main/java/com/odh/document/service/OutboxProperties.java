package com.odh.document.service;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * 아웃박스 발행 튜닝값. 이벤트 전달 지연과 처리량을 정한다.
 *
 * @param batch        한 번에 Redis Stream 으로 옮기는 최대 이벤트 수
 * @param pollInterval 발행 주기. {@code @Scheduled} 가 같은 키({@code document.outbox.poll-interval})를 직접 읽는다 —
 *                     여기 둔 것은 문서화와 기동 시 검증을 위해서다
 */
@ConfigurationProperties(prefix = "document.outbox")
public record OutboxProperties(
        @DefaultValue("100") int batch,
        @DefaultValue("2s") Duration pollInterval
) {
    public OutboxProperties {
        if (batch < 1) {
            throw new IllegalArgumentException("document.outbox.batch 는 1 이상이어야 한다: " + batch);
        }
        if (pollInterval == null || pollInterval.isZero() || pollInterval.isNegative()) {
            throw new IllegalArgumentException("document.outbox.poll-interval 은 0 보다 커야 한다: " + pollInterval);
        }
    }
}

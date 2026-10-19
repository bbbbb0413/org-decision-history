package com.odh.chat.web;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * 답변 SSE 연결 튜닝값. 앞단 프록시·로드밸런서의 idle 타임아웃에 맞춰야 해서 배포 환경마다 다르다.
 *
 * @param timeout     SSE 연결 상한. 게이트웨이 응답 타임아웃(300초)·spring.mvc.async.request-timeout 과 맞춘다
 * @param heartbeat   하트비트 간격. 프록시 idle 타임아웃보다 짧아야 연결이 끊기지 않는다
 * @param streamBlock Redis Stream 을 한 번 읽을 때 기다리는 시간 (POLL 전용)
 * @param relayMode   릴레이 방식. POLL 은 롤백·전후 비교용이다 ({@link RelayMode})
 * @param drainBatchSize PUBSUB 에서 Stream 을 한 번 읽을 때 가져오는 최대 레코드 수
 */
@ConfigurationProperties(prefix = "chat.sse")
public record SseProperties(
        @DefaultValue("5m") Duration timeout,
        @DefaultValue("15s") Duration heartbeat,
        @DefaultValue("5s") Duration streamBlock,
        @DefaultValue("PUBSUB") RelayMode relayMode,
        @DefaultValue("100") int drainBatchSize
) {
    public SseProperties {
        requirePositive("chat.sse.timeout", timeout);
        requirePositive("chat.sse.heartbeat", heartbeat);
        requirePositive("chat.sse.stream-block", streamBlock);
        if (drainBatchSize <= 0) {
            throw new IllegalArgumentException("chat.sse.drain-batch-size 는 0 보다 커야 한다: " + drainBatchSize);
        }
        if (heartbeat.compareTo(timeout) >= 0) {
            throw new IllegalArgumentException("chat.sse.heartbeat(" + heartbeat + ") 는 timeout(" + timeout + ") 보다 짧아야 한다");
        }
    }

    static void requirePositive(String name, Duration value) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException(name + " 는 0 보다 커야 한다: " + value);
        }
    }
}

package com.odh.auth.metrics;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * 제품 지표 화면의 정책값.
 *
 * @param latencyTarget 일반 질문 응답 목표(기능명세서 핵심 지표 4). 이보다 빨리 끝난 답변의 비율을 낸다
 * @param maxDays       조회 기간 상한. 1년을 넘는 집계는 대시보드 용도를 벗어나고 스캔 비용만 커진다
 * @param defaultDays   기간을 주지 않았거나 0 이하일 때의 기간
 */
@ConfigurationProperties(prefix = "metrics.product")
public record MetricsProperties(
        @DefaultValue("5s") Duration latencyTarget,
        @DefaultValue("365") int maxDays,
        @DefaultValue("30") int defaultDays
) {
    public MetricsProperties {
        if (latencyTarget == null || latencyTarget.isZero() || latencyTarget.isNegative()) {
            throw new IllegalArgumentException("metrics.product.latency-target 은 0 보다 커야 한다: " + latencyTarget);
        }
        if (defaultDays < 1 || maxDays < defaultDays) {
            throw new IllegalArgumentException(
                    "metrics.product 는 1 <= default-days(" + defaultDays + ") <= max-days(" + maxDays + ") 여야 한다");
        }
    }
}

package com.odh.chat.timeline;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * 타임라인·상충 비교 튜닝값. LLM 호출량과 직결된다.
 *
 * @param conflictTtl         상충 분석 캐시 유효 시간. 문서가 자주 바뀌지 않아 하루면 충분하다
 * @param maxEvents           타임라인에 싣는 문서 수 상한. 상충 비교는 이 문서들의 쌍마다 돈다
 * @param maxDifferenceLength 상충 차이 설명의 최대 글자 수
 */
@ConfigurationProperties(prefix = "chat.timeline")
public record TimelineProperties(
        @DefaultValue("24h") Duration conflictTtl,
        @DefaultValue("30") int maxEvents,
        @DefaultValue("500") int maxDifferenceLength
) {
    public TimelineProperties {
        if (conflictTtl == null || conflictTtl.isZero() || conflictTtl.isNegative()) {
            throw new IllegalArgumentException("chat.timeline.conflict-ttl 은 0 보다 커야 한다: " + conflictTtl);
        }
        if (maxEvents < 1) {
            throw new IllegalArgumentException("chat.timeline.max-events 는 1 이상이어야 한다: " + maxEvents);
        }
        // 잘라낸 끝에 "..." 를 붙이므로 그보다 길어야 한다
        if (maxDifferenceLength < 10) {
            throw new IllegalArgumentException("chat.timeline.max-difference-length 는 10 이상이어야 한다: " + maxDifferenceLength);
        }
    }

    public static TimelineProperties defaults() {
        return new TimelineProperties(Duration.ofHours(24), 30, 500);
    }
}

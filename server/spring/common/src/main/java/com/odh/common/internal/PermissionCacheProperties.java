package com.odh.common.internal;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

import java.time.Duration;

/**
 * 권한 범위 캐시({@link PermissionScopeClient}) 튜닝값. 모든 서비스가 같은 키를 쓴다 — 기존 공통 키
 * ({@code auth.service-url}, {@code internal.api-key})처럼 서비스 접두어 없이 둔다.
 * 권한 변경이 반영되기까지의 지연과 auth-service 부하 사이의 트레이드오프다.
 *
 * @param ttl        캐시 유효 시간. 권한을 바꾸면 최대 이만큼 뒤에 반영된다
 * @param maxEntries 캐시 항목 상한. 넘치면 오래된 것부터 밀려난다
 */
@ConfigurationProperties(prefix = "permission-cache")
public record PermissionCacheProperties(
        @DefaultValue("10s") Duration ttl,
        @DefaultValue("10000") long maxEntries
) {
    public PermissionCacheProperties {
        if (ttl == null || ttl.isZero() || ttl.isNegative()) {
            throw new IllegalArgumentException("permission-cache.ttl 은 0 보다 커야 한다: " + ttl);
        }
        if (maxEntries < 1) {
            throw new IllegalArgumentException("permission-cache.max-entries 는 1 이상이어야 한다: " + maxEntries);
        }
    }
}

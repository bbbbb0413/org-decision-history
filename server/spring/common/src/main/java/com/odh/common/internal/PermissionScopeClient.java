package com.odh.common.internal;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.odh.common.InternalAuth;
import com.odh.common.PermissionScope;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestClient;

/**
 * 사용자의 읽기 범위를 auth-service 에 물어본다.
 *
 * document-service·chat-service 가 각자 들고 있던 동일한 클라이언트를 여기로 합쳤다.
 * 권한 판정을 한 서비스가 소유하게 두고 나머지는 물어보는 구조다.
 * 각 서비스가 권한 테이블을 직접 읽으면 판정 규칙이 갈라져 서로 어긋난다.
 *
 * 요청마다 호출하면 왕복이 늘어나므로 짧게 캐시한다. 권한 변경은 새 요청부터
 * 적용하면 되므로 TTL 을 초 단위로 두는 것으로 충분하다.
 */
public class PermissionScopeClient {

    private static final Logger log = LoggerFactory.getLogger(PermissionScopeClient.class);

    private final RestClient client;
    private final String internalKey;
    // 수동 TTL 검사로는 오래된 항목이 지워지지 않고 계속 쌓인다.
    // Caffeine 이 TTL 만료·최대 크기 둘 다 알아서 쫓아낸다
    // 크기·유효 시간은 PermissionCacheProperties(permission-cache.*)에서 온다
    private final Cache<Long, PermissionScope> cache;

    public PermissionScopeClient(RestClient.Builder builder,
                                 @Value("${auth.service-url}") String authUrl,
                                 @Value("${internal.api-key:}") String internalKey,
                                 PermissionCacheProperties cacheProps) {
        this.client = builder.baseUrl(authUrl).build();
        this.internalKey = internalKey;
        this.cache = Caffeine.newBuilder()
                .maximumSize(cacheProps.maxEntries())
                .expireAfterWrite(cacheProps.ttl())
                .build();
    }

    public PermissionScope scopeOf(Long userId) {
        PermissionScope cached = cache.getIfPresent(userId);
        if (cached != null) {
            return cached;
        }
        try {
            PermissionScope scope = client.get()
                    .uri("/internal/permission-scope/{userId}", userId)
                    .header(InternalAuth.API_KEY_HEADER, internalKey)
                    .retrieve()
                    .body(PermissionScope.class);

            PermissionScope resolved = scope == null ? PermissionScope.empty() : scope;
            cache.put(userId, resolved);
            return resolved;
        } catch (Exception e) {
            // 권한을 확인할 수 없으면 아무것도 보여주지 않는다.
            // 판정 실패를 전체 허용으로 처리하면 그 순간이 곧 정보 유출이다.
            log.warn("권한 범위 조회 실패. 빈 범위로 처리한다. userId={} 사유={}", userId, e.toString());
            return PermissionScope.empty();
        }
    }
}

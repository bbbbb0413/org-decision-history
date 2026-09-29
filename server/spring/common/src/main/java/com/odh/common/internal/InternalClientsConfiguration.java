package com.odh.common.internal;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

/**
 * auth-service 를 내부 호출하는 클라이언트 배선. document-service·chat-service 만
 * {@code @Import}한다 — auth-service 는 이 엔드포인트들의 주인이라 스스로 호출할 필요가
 * 없고, index-worker 는 애초에 권한 판정·감사 기록을 하지 않는다.
 */
@Configuration
public class InternalClientsConfiguration {

    @Bean
    public AuditClient auditClient(RestClient.Builder builder,
                                   @Value("${auth.service-url}") String authUrl,
                                   @Value("${internal.api-key:}") String internalKey) {
        return new AuditClient(builder, authUrl, internalKey);
    }

    @Bean
    public PermissionScopeClient permissionScopeClient(RestClient.Builder builder,
                                                       @Value("${auth.service-url}") String authUrl,
                                                       @Value("${internal.api-key:}") String internalKey) {
        return new PermissionScopeClient(builder, authUrl, internalKey);
    }
}

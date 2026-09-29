package com.odh.gateway.config;

import com.odh.common.Correlation;
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.cloud.gateway.route.RouteLocator;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 라우팅 규칙.
 *
 * 설정 파일 대신 코드로 정의한 이유는 Spring Cloud Gateway 의 프로퍼티 경로가
 * 버전에 따라 바뀌어 왔기 때문이다. 코드는 컴파일 시점에 깨지므로 조용히 어긋나지 않는다.
 */
@Configuration
public class RouteConfiguration {

    /**
     * 게이트웨이와 하위 서비스가 각각 상관관계 ID 헤더를 넣으므로 응답에 두 번 실린다.
     * 게이트웨이가 발급한 값이 먼저이므로 첫 값만 남긴다.
     */
    private static final String DEDUPE_STRATEGY = "RETAIN_FIRST";

    @Bean
    public RouteLocator routes(RouteLocatorBuilder builder, GatewayProperties props,
                                RedisRateLimiter loginRateLimiter, KeyResolver clientIpKeyResolver) {
        return builder.routes()

                // ① 인증 — 로그인은 인증 전 공개 엔드포인트라 IP당 rate limit을 건다(무차별
                // 대입 공격 방어). 더 구체적인 경로라 아래 일반 auth 라우트보다 먼저 와야 한다
                .route("auth-login", r -> r
                        .path("/api/auth/login")
                        .filters(f -> f
                                .dedupeResponseHeader(Correlation.HEADER, DEDUPE_STRATEGY)
                                .requestRateLimiter(c -> c
                                        .setRateLimiter(loginRateLimiter)
                                        .setKeyResolver(clientIpKeyResolver)))
                        .uri(props.authUrl()))

                // ① 인증 — 재발급, 내 정보, JWKS
                .route("auth", r -> r
                        .path("/api/auth/**", "/.well-known/**")
                        .filters(f -> f.dedupeResponseHeader(Correlation.HEADER, DEDUPE_STRATEGY))
                        .uri(props.authUrl()))

                // ② 조직 · 권한 — 소속과 역할, 접근 정책, 감사, 운영 현황, 팀별 알림 채널
                .route("admin", r -> r
                        .path("/api/organizations/**", "/api/users/**", "/api/teams/**",
                                "/api/access-policies/**", "/api/audit-logs/**", "/api/admin/**")
                        .filters(f -> f.dedupeResponseHeader(Correlation.HEADER, DEDUPE_STRATEGY))
                        .uri(props.authUrl()))

                // ③ 문서 — 문서, 원본 파일, 가져오기, 외부 저장소, 거버넌스 상충
                .route("document", r -> r
                        .path("/api/documents/**", "/api/document-files/**",
                                "/api/document-imports/**", "/api/repositories/**",
                                "/api/governance/**")
                        .filters(f -> f.dedupeResponseHeader(Correlation.HEADER, DEDUPE_STRATEGY))
                        .uri(props.documentUrl()))

                // ④ 색인 작업
                .route("index", r -> r
                        .path("/api/index-jobs/**")
                        .filters(f -> f.dedupeResponseHeader(Correlation.HEADER, DEDUPE_STRATEGY))
                        .uri(props.indexUrl()))

                // ⑤ 대화 · 답변 · 타임라인 (SSE 스트리밍 포함)
                .route("chat", r -> r
                        .path("/api/conversations/**", "/api/messages/**", "/api/timeline/**")
                        .filters(f -> f.dedupeResponseHeader(Correlation.HEADER, DEDUPE_STRATEGY))
                        .uri(props.chatUrl()))

                .build();
    }
}

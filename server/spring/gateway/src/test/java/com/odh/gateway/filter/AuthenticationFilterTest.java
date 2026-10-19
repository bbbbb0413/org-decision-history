package com.odh.gateway.filter;

import com.odh.common.InternalAuth;
import com.odh.gateway.config.GatewayProperties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * 전체 시스템의 유일한 인가 게이트. public 경로 목록, 액세스/재발급
 * 라우팅, X-User-Id 스푸핑 방지를 검증한다 — 여기 회귀가 나면 인증이 조용히 뚫린다.
 */
class AuthenticationFilterTest {

    private final GatewayProperties props = new GatewayProperties(
            "http://auth", "http://doc", "http://index", "http://chat", "internal-secret",
            "http://jwks", "http://localhost:5173", 1, 5);

    private JwtVerifier verifier;
    private GatewayFilterChain chain;
    private AuthenticationFilter filter;

    @BeforeEach
    void setUp() {
        verifier = mock(JwtVerifier.class);
        chain = mock(GatewayFilterChain.class);
        when(chain.filter(any())).thenReturn(Mono.empty());
        filter = new AuthenticationFilter(verifier, props);
    }

    @Test
    void publicPath_bypassesAuthAndForwardsInternalKeyOnly() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/auth/login").build());

        filter.filter(exchange, chain).block();

        ServerHttpRequest forwarded = forwardedRequest();
        assertThat(forwarded.getHeaders().getFirst(InternalAuth.API_KEY_HEADER)).isEqualTo("internal-secret");
        assertThat(forwarded.getHeaders().getFirst(InternalAuth.USER_ID_HEADER)).isNull();
        verifyNoInteractions(verifier);
    }

    @Test
    void missingAuthorizationHeader_isRejectedWithoutCallingChain() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/documents/1").build());

        filter.filter(exchange, chain).block();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        verify(chain, never()).filter(any());
    }

    @Test
    void validAccessToken_forwardsVerifiedIdentity_overridingClientSuppliedHeader() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/documents/1")
                        .header("Authorization", "Bearer good-token")
                        // 클라이언트가 스스로 관리자 행세를 하려는 시도 — 반드시 덮어써야 한다
                        .header(InternalAuth.USER_ID_HEADER, "999")
                        .build());
        when(verifier.verify("good-token"))
                .thenReturn(Mono.just(new JwtVerifier.Claims("42", List.of("MEMBER"), null, 0)));

        filter.filter(exchange, chain).block();

        ServerHttpRequest forwarded = forwardedRequest();
        assertThat(forwarded.getHeaders().getFirst(InternalAuth.USER_ID_HEADER)).isEqualTo("42");
        assertThat(forwarded.getHeaders().getFirst(InternalAuth.USER_ROLES_HEADER)).isEqualTo("MEMBER");
        assertThat(forwarded.getHeaders().getFirst(InternalAuth.API_KEY_HEADER)).isEqualTo("internal-secret");
    }

    @Test
    void refreshTokenOnNonRefreshPath_isRejected() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/documents/1")
                        .header("Authorization", "Bearer refresh-token")
                        .build());
        when(verifier.verify("refresh-token"))
                .thenReturn(Mono.just(new JwtVerifier.Claims("42", List.of(), "refresh", 0)));

        filter.filter(exchange, chain).block();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        verify(chain, never()).filter(any());
    }

    @Test
    void accessTokenOnRefreshPath_isRejected() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.post("/api/auth/refresh")
                        .header("Authorization", "Bearer access-token")
                        .build());
        when(verifier.verify("access-token"))
                .thenReturn(Mono.just(new JwtVerifier.Claims("42", List.of("MEMBER"), null, 0)));

        filter.filter(exchange, chain).block();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        verify(chain, never()).filter(any());
    }

    @Test
    void refreshTokenOnRefreshPath_isAllowed() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.post("/api/auth/refresh")
                        .header("Authorization", "Bearer refresh-token")
                        .build());
        when(verifier.verify("refresh-token"))
                .thenReturn(Mono.just(new JwtVerifier.Claims("42", List.of(), "refresh", 0)));

        filter.filter(exchange, chain).block();

        verify(chain).filter(any());
    }

    @Test
    void expiredToken_respondsWithTokenExpiredCode() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/documents/1")
                        .header("Authorization", "Bearer expired-token")
                        .build());
        when(verifier.verify("expired-token")).thenReturn(Mono.error(new JwtVerifier.TokenExpiredException()));

        filter.filter(exchange, chain).block();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(responseBody(exchange)).contains("TOKEN_EXPIRED");
        verify(chain, never()).filter(any());
    }

    @Test
    void revokedToken_respondsWithLogoutMessage() {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/documents/1")
                        .header("Authorization", "Bearer revoked-token")
                        .build());
        when(verifier.verify("revoked-token")).thenReturn(Mono.error(new JwtVerifier.TokenRevokedException()));

        filter.filter(exchange, chain).block();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(responseBody(exchange)).contains("TOKEN_INVALID").contains("로그아웃된 토큰");
        verify(chain, never()).filter(any());
    }

    /**
     * 키 조회·Redis 같은 검증 기반이 실패하면 503 이다. 401 로 답하면 프론트가 재발급을 시도하고,
     * 재발급도 같은 이유로 실패해 사용자를 로그아웃시킨다.
     */
    @Test
    void keySourceFailure_respondsWith503NotTokenInvalid() {
        assertServiceUnavailable(new com.nimbusds.jose.jwk.source.JWKSetUnavailableException("jwks down", null));
    }

    @Test
    void redisFailure_respondsWith503NotTokenInvalid() {
        assertServiceUnavailable(new org.springframework.data.redis.RedisConnectionFailureException("redis down"));
    }

    @Test
    void forgedOrMalformedToken_isStill401TokenInvalid() {
        for (Throwable e : List.<Throwable>of(
                new com.nimbusds.jose.proc.BadJWSException("Signed JWT rejected: Invalid signature"),
                new java.text.ParseException("Invalid serialized JWT", 0))) {
            MockServerWebExchange exchange = MockServerWebExchange.from(
                    MockServerHttpRequest.get("/api/documents/1").header("Authorization", "Bearer bad").build());
            when(verifier.verify("bad")).thenReturn(Mono.error(e));

            filter.filter(exchange, chain).block();

            assertThat(exchange.getResponse().getStatusCode()).as(e.toString()).isEqualTo(HttpStatus.UNAUTHORIZED);
            assertThat(responseBody(exchange)).contains("TOKEN_INVALID");
        }
        verify(chain, never()).filter(any());
    }

    private void assertServiceUnavailable(Throwable failure) {
        MockServerWebExchange exchange = MockServerWebExchange.from(
                MockServerHttpRequest.get("/api/documents/1").header("Authorization", "Bearer good").build());
        when(verifier.verify("good")).thenReturn(Mono.error(failure));

        filter.filter(exchange, chain).block();

        assertThat(exchange.getResponse().getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(responseBody(exchange)).contains("AUTH_UNAVAILABLE").doesNotContain("TOKEN_INVALID");
        verify(chain, never()).filter(any());
    }

    private ServerHttpRequest forwardedRequest() {
        ArgumentCaptor<ServerWebExchange> captor = ArgumentCaptor.forClass(ServerWebExchange.class);
        verify(chain).filter(captor.capture());
        return captor.getValue().getRequest();
    }

    private String responseBody(MockServerWebExchange exchange) {
        return ((org.springframework.mock.http.server.reactive.MockServerHttpResponse) exchange.getResponse())
                .getBodyAsString().block();
    }
}

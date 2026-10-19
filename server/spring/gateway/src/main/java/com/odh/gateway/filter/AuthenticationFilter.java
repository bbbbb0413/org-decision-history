package com.odh.gateway.filter;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.KeySourceException;
import com.nimbusds.jose.proc.BadJOSEException;
import com.odh.common.Correlation;
import com.odh.common.ErrorCode;
import com.odh.common.InternalAuth;
import com.odh.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.server.reactive.ServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.text.ParseException;
import java.util.List;

/**
 * JWT 검증과 사용자 정보 전달.
 *
 * 검증에 성공하면 사용자 식별자와 역할을 헤더로 하위 서비스에 넘기고, 내부 API 키를 붙인다.
 * 하위 서비스는 이 키가 없는 요청을 거부하므로 게이트웨이 우회 호출이 차단된다.
 * 클라이언트가 보낸 X-User-Id 는 신뢰하지 않고 항상 덮어쓴다.
 */
@Component
public class AuthenticationFilter implements GlobalFilter, Ordered {

    private static final Logger log = LoggerFactory.getLogger(AuthenticationFilter.class);
    private static final String BEARER = "Bearer ";

    /** 인증 없이 통과시키는 경로 */
    private static final List<String> PUBLIC_PATHS = List.of(
            "/api/auth/login",
            "/.well-known/",
            "/actuator/health"
    );

    /** 재발급 전용 토큰으로만 접근할 수 있는 경로 */
    private static final String REFRESH_PATH = "/api/auth/refresh";

    private final JwtVerifier verifier;
    private final GatewayProperties properties;

    public AuthenticationFilter(JwtVerifier verifier, GatewayProperties properties) {
        this.verifier = verifier;
        this.properties = properties;
    }

    @Override
    public int getOrder() {
        // 상관관계 ID 필터 다음에 실행한다
        return Ordered.HIGHEST_PRECEDENCE + 10;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String path = exchange.getRequest().getPath().value();

        if (isPublic(path)) {
            return chain.filter(withInternalKey(exchange, null, List.of()));
        }

        String header = exchange.getRequest().getHeaders().getFirst(HttpHeaders.AUTHORIZATION);
        if (header == null || !header.startsWith(BEARER)) {
            return reject(exchange, ErrorCode.TOKEN_INVALID, "인증 토큰이 필요합니다.");
        }

        // verify()가 로그아웃 워터마크를 Redis에서 확인하므로 리액티브 체인이다
        return verifier.verify(header.substring(BEARER.length()))
                .flatMap(claims -> {
                    // 재발급 토큰은 재발급 경로에서만, 액세스 토큰은 그 외 경로에서만 쓴다
                    boolean refreshPath = REFRESH_PATH.equals(path);
                    if (refreshPath != claims.isRefresh()) {
                        return reject(exchange, ErrorCode.TOKEN_INVALID, "이 경로에 사용할 수 없는 토큰입니다.");
                    }
                    return chain.filter(withInternalKey(exchange, claims.userId(), claims.roles()));
                })
                .onErrorResume(JwtVerifier.TokenExpiredException.class,
                        e -> reject(exchange, ErrorCode.TOKEN_EXPIRED, "인증이 만료되었습니다. 다시 로그인해 주세요."))
                .onErrorResume(JwtVerifier.TokenRevokedException.class,
                        e -> reject(exchange, ErrorCode.TOKEN_INVALID, "로그아웃된 토큰입니다. 다시 로그인해 주세요."))
                .onErrorResume(e -> {
                    if (isMalformedOrForged(e)) {
                        log.debug("토큰 검증 실패: {}", e.getMessage());
                        return reject(exchange, ErrorCode.TOKEN_INVALID, "인증 토큰이 올바르지 않습니다.");
                    }
                    // 토큰이 아니라 검증 기반(키 조회·Redis)이 실패했다. 401 로 답하면 프론트가 재발급을
                    // 시도하고, 재발급도 같은 이유로 실패해 사용자를 로그아웃시킨다 — 503 으로 구분한다
                    log.warn("토큰 검증 중 오류 {}: {}", e.getClass().getName(), e.getMessage());
                    return reject(exchange, HttpStatus.SERVICE_UNAVAILABLE, ErrorCode.AUTH_UNAVAILABLE,
                            "지금은 인증을 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.");
                });
    }

    /**
     * 토큰 자체가 잘못된 경우(형식·서명·클레임). 그 밖의 예외(키 조회 {@code KeySourceException},
     * Redis 오류 등)는 검증 기반의 실패다 — 토큰이 맞는지 확인하지 못했을 뿐이다.
     */
    static boolean isMalformedOrForged(Throwable e) {
        return e instanceof BadJOSEException
                || e instanceof ParseException
                || (e instanceof JOSEException && !(e instanceof KeySourceException));
    }

    private boolean isPublic(String path) {
        return PUBLIC_PATHS.stream().anyMatch(path::startsWith);
    }

    private ServerWebExchange withInternalKey(ServerWebExchange exchange, String userId, List<String> roles) {
        ServerHttpRequest.Builder builder = exchange.getRequest().mutate()
                .headers(h -> {
                    h.remove(InternalAuth.USER_ID_HEADER);
                    h.remove(InternalAuth.USER_ROLES_HEADER);
                })
                .header(InternalAuth.API_KEY_HEADER, properties.internalKey());

        if (userId != null) {
            builder.header(InternalAuth.USER_ID_HEADER, userId);
            builder.header(InternalAuth.USER_ROLES_HEADER, String.join(",", roles));
        }
        return exchange.mutate().request(builder.build()).build();
    }

    private Mono<Void> reject(ServerWebExchange exchange, String code, String message) {
        return reject(exchange, HttpStatus.UNAUTHORIZED, code, message);
    }

    private Mono<Void> reject(ServerWebExchange exchange, HttpStatus status, String code, String message) {
        var response = exchange.getResponse();
        response.setStatusCode(status);
        response.getHeaders().setContentType(MediaType.APPLICATION_JSON);

        String correlationId = exchange.getResponse().getHeaders().getFirst(Correlation.HEADER);
        String body = """
                {"code":"%s","message":"%s","correlationId":"%s"}
                """.formatted(code, message, correlationId == null ? "" : correlationId);

        DataBuffer buffer = response.bufferFactory().wrap(body.getBytes(StandardCharsets.UTF_8));
        return response.writeWith(Mono.just(buffer));
    }
}

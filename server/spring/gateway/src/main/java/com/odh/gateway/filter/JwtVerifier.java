package com.odh.gateway.filter;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.jwk.source.CachingJWKSetSource;
import com.nimbusds.jose.jwk.source.OutageTolerantJWKSetSource;
import com.nimbusds.jose.jwk.source.RefreshAheadCachingJWKSetSource;
import com.nimbusds.jose.jwk.source.RetryingJWKSetSource;
import com.nimbusds.jose.proc.JWSVerificationKeySelector;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jose.util.events.Event;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.proc.DefaultJWTProcessor;
import com.nimbusds.jwt.proc.ExpiredJWTException;
import com.odh.common.TokenRevocation;
import com.odh.gateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;
import reactor.core.scheduler.Schedulers;

import java.net.URI;
import java.net.URL;
import java.time.Duration;
import java.util.Date;
import java.util.List;

/**
 * auth-service 의 JWKS 로 토큰 서명과 만료를 검증한다.
 *
 * {@link GatewayKeySource} 가 공개키를 캐시하고, 모르는 kid 를 만나면 다시 받아온다.
 * auth-service 가 재시작해 키가 바뀌어도 게이트웨이를 함께 내리지 않아도 된다.
 *
 * 키 조회가 검증을 멈추지 않게 네 가지를 둔다:
 * - 검증을 Netty 이벤트 루프 밖(boundedElastic)에서 돈다. 키 조회는 블로킹 HTTP 이고, 갱신 중인 다른
 *   요청은 갱신 락을 최대 15초 기다린다 — 이벤트 루프에서 돌면 그 루프의 모든 연결이 함께 멈춘다.
 * - 만료 전에 백그라운드에서 갱신한다(스케줄). 요청 경로에서 조회하는 일은 기동 직후와 모르는 kid 뿐이다.
 * - auth-service 가 응답하지 않으면 마지막으로 받은 키를 계속 쓴다(장애 허용 1시간). 없으면 캐시가
 *   만료되는 순간 조회가 한 번 실패할 때 재조회 제한 동안 모든 토큰이 거절된다.
 * - "모르는 키"(401)와 "키를 확인하지 못함"(503)을 가른다({@link GatewayKeySource}, {@link AuthenticationFilter}).
 */
@Component
public class JwtVerifier implements DisposableBean {

    private static final Logger log = LoggerFactory.getLogger(JwtVerifier.class);

    private final DefaultJWTProcessor<SecurityContext> processor;
    private final GatewayKeySource keySource;
    private final ReactiveStringRedisTemplate redis;

    @Autowired   // 테스트용 생성자가 하나 더 있어 Spring 이 고를 생성자를 밝힌다
    public JwtVerifier(GatewayProperties properties, ReactiveStringRedisTemplate redis) throws Exception {
        this(URI.create(properties.jwksUri()).toURL(), KeySourceTiming.DEFAULT, redis);
    }

    /** 테스트가 캐시·장애 허용 시간을 줄여 쓰려고 연다. */
    JwtVerifier(URL jwksUrl, KeySourceTiming timing, ReactiveStringRedisTemplate redis) {
        this.keySource = new GatewayKeySource(jwksUrl, timing, JwtVerifier::logKeySourceEvent);
        this.processor = new DefaultJWTProcessor<>();
        this.processor.setJWSKeySelector(new JWSVerificationKeySelector<>(JWSAlgorithm.RS256, keySource));
        this.redis = redis;
    }

    /**
     * @param cacheTtl                  키 캐시 수명
     * @param refreshAhead              만료 이만큼 전에 백그라운드에서 갱신한다
     * @param refreshWait               다른 요청이 갱신 중일 때 기다리는 최대 시간
     * @param unknownKidRefreshInterval 모르는 kid 로 다시 받아오는 최소 간격({@link GatewayKeySource})
     * @param outageTtl                 auth-service 가 응답하지 않을 때 마지막 키를 계속 쓰는 시간
     * @param httpTimeout               JWKS 조회 연결·읽기 타임아웃
     * @param failureBackoff            조회가 실패하면 이 시간 동안 다시 묻지 않는다({@link GatewayKeySource})
     */
    record KeySourceTiming(Duration cacheTtl, Duration refreshAhead, Duration refreshWait,
                           Duration unknownKidRefreshInterval, Duration outageTtl, Duration httpTimeout,
                           Duration failureBackoff) {
        static final KeySourceTiming DEFAULT = new KeySourceTiming(
                Duration.ofMinutes(5), Duration.ofSeconds(30), Duration.ofSeconds(15),
                Duration.ofSeconds(30), Duration.ofHours(1), Duration.ofSeconds(2), Duration.ofSeconds(2));
    }

    @Override
    public void destroy() throws Exception {
        keySource.close();
    }

    /**
     * 키 조회가 실패했거나(재시도·장애 허용·갱신 실패) 갱신 대기가 시간 초과된 이벤트는 사유와 함께 WARN 으로,
     * 갱신을 기다리는 요청마다 나는 대기 이벤트는 DEBUG 로, 나머지(정상 갱신, 5분에 몇 번)는 INFO 로 남긴다.
     */
    private static void logKeySourceEvent(Event<?, SecurityContext> event) {
        String name = event.getClass().getSimpleName();
        Exception cause = switch (event) {
            case RetryingJWKSetSource.RetrialEvent<?> e -> e.getException();
            case OutageTolerantJWKSetSource.OutageEvent<?> e -> e.getException();
            case RefreshAheadCachingJWKSetSource.ScheduledRefreshFailed<?> e -> e.getException();
            default -> null;
        };
        if (cause != null) {
            log.warn("JWKS 키 소스 이벤트: {} — {}", name, cause.toString());
        } else if (event instanceof CachingJWKSetSource.RefreshTimedOutEvent<?>
                || event instanceof CachingJWKSetSource.UnableToRefreshEvent<?>
                || event instanceof RefreshAheadCachingJWKSetSource.UnableToRefreshAheadOfExpirationEvent<?>) {
            log.warn("JWKS 키 소스 이벤트: {}", name);
        } else if (event instanceof CachingJWKSetSource.WaitingForRefreshEvent<?>) {
            log.debug("JWKS 키 소스 이벤트: {}", name);
        } else {
            log.info("JWKS 키 소스 이벤트: {}", name);
        }
    }

    /**
     * 검증에 성공하면 클레임을, 실패하면 에러 시그널을 담은 Mono 를 돌려준다.
     *
     * DefaultJWTProcessor 는 기본 DefaultJWTClaimsVerifier 를 자동으로 붙여 만료를 먼저
     * 검사하고, 이미 만료된 토큰에는 process() 단계에서 자체 ExpiredJWTException 을
     * 던진다 — 아래의 수동 만료 검사에 도달하지 못한다. 그 예외를 여기서 잡아
     * TokenExpiredException 으로 바꿔야 AuthenticationFilter 가 TOKEN_EXPIRED 와
     * TOKEN_INVALID 를 구분해 응답한다. 이 변환이 없으면 만료된 토큰도 위조 토큰과
     * 같은 오류로 보인다.
     *
     * 서명·만료 검사를 통과해도 로그아웃 워터마크({@link TokenRevocation})보다
     * 먼저 발급된 토큰이면 거부한다 — Redis 조회가 필요해 이 메서드 전체가 리액티브다.
     *
     * 서명 검증은 이벤트 루프 밖에서 돈다(클래스 주석). 서명 검증 자체는 짧지만, 키 조회가 끼면 블로킹이다.
     */
    public Mono<Claims> verify(String token) {
        return Mono.fromCallable(() -> verifySignatureAndExpiry(token))
                .subscribeOn(Schedulers.boundedElastic())
                .flatMap(this::rejectIfRevoked);
    }

    private Claims verifySignatureAndExpiry(String token) throws Exception {
        JWTClaimsSet claims;
        try {
            claims = processor.process(token, null);
        } catch (ExpiredJWTException e) {
            throw new TokenExpiredException();
        }

        Date expiry = claims.getExpirationTime();
        if (expiry == null || expiry.before(new Date())) {
            throw new TokenExpiredException();
        }
        Date issuedAt = claims.getIssueTime();
        if (issuedAt == null) {
            throw new TokenExpiredException();
        }

        Object roles = claims.getClaim("roles");
        List<String> roleNames = roles instanceof List<?> list
                ? list.stream().map(String::valueOf).toList()
                : List.of();

        return new Claims(claims.getSubject(), roleNames, claims.getStringClaim("typ"),
                issuedAt.toInstant().getEpochSecond());
    }

    private Mono<Claims> rejectIfRevoked(Claims claims) {
        return redis.opsForValue().get(TokenRevocation.watermarkKey(claims.userId()))
                .defaultIfEmpty("")
                .flatMap(revokedAt -> {
                    if (!revokedAt.isBlank() && claims.issuedAtEpochSeconds() <= Long.parseLong(revokedAt)) {
                        return Mono.error(new TokenRevokedException());
                    }
                    return Mono.just(claims);
                });
    }

    /**
     * @param userId  토큰 subject. 하위 서비스에 X-User-Id 로 전달한다
     * @param roles   역할 목록. 화면 노출 판정에 쓴다
     * @param type    refresh 이면 재발급 전용 토큰이다
     * @param issuedAtEpochSeconds 발급 시각(iat). 로그아웃 워터마크와 비교한다
     */
    public record Claims(String userId, List<String> roles, String type, long issuedAtEpochSeconds) {
        public boolean isRefresh() {
            return "refresh".equals(type);
        }
    }

    public static class TokenExpiredException extends RuntimeException {
    }

    /** 로그아웃 이후 발급 시각이 워터마크보다 이르다고 판정된 토큰 */
    public static class TokenRevokedException extends RuntimeException {
    }
}

package com.odh.gateway.filter;

import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.jwk.source.JWKSourceBuilder;
import com.nimbusds.jose.proc.JWSVerificationKeySelector;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.proc.DefaultJWTProcessor;
import com.nimbusds.jwt.proc.ExpiredJWTException;
import com.odh.common.TokenRevocation;
import com.odh.gateway.config.GatewayProperties;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;

import java.net.URI;
import java.net.URL;
import java.util.Date;
import java.util.List;

/**
 * auth-service 의 JWKS 로 토큰 서명과 만료를 검증한다.
 *
 * JWKSourceBuilder 가 공개키를 캐시하고, 모르는 kid 를 만나면 다시 받아온다.
 * auth-service 가 재시작해 키가 바뀌어도 게이트웨이를 함께 내리지 않아도 된다.
 */
@Component
public class JwtVerifier {

    private final DefaultJWTProcessor<SecurityContext> processor;
    private final ReactiveStringRedisTemplate redis;

    public JwtVerifier(GatewayProperties properties, ReactiveStringRedisTemplate redis) throws Exception {
        URL jwksUrl = URI.create(properties.jwksUri()).toURL();
        JWKSource<SecurityContext> keySource = JWKSourceBuilder.create(jwksUrl).build();

        this.processor = new DefaultJWTProcessor<>();
        this.processor.setJWSKeySelector(new JWSVerificationKeySelector<>(JWSAlgorithm.RS256, keySource));
        this.redis = redis;
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
     */
    public Mono<Claims> verify(String token) {
        return Mono.fromCallable(() -> verifySignatureAndExpiry(token))
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

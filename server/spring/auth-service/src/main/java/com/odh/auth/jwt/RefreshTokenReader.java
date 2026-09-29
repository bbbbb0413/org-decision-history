package com.odh.auth.jwt;

import com.nimbusds.jose.crypto.RSASSAVerifier;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.springframework.stereotype.Component;

import java.time.Instant;

/**
 * 재발급 토큰에서 `jti` 와 만료 시각을 읽는다 (회전에 필요하다).
 *
 * <h2>왜 auth-service 가 토큰을 다시 읽는가</h2>
 *
 * 게이트웨이가 이미 서명·만료·로그아웃 워터마크를 검증하고 사용자 식별자만 헤더로 넘긴다.
 * 그런데 회전은 "이 토큰"을 식별해야 하므로 `jti` 가 필요하고, 그 값은 헤더에 없다.
 *
 * 다행히 게이트웨이가 `Authorization` 헤더를 지우지 않는다
 * ({@code AuthenticationFilter.withInternalKey} 는 내부 사용자 헤더만 손댄다).
 * 그래서 게이트웨이를 고치지 않고 여기서 토큰을 그대로 읽을 수 있다.
 *
 * <h2>이미 검증했는데 또 검증하는 이유</h2>
 *
 * 서명을 다시 확인한다. auth-service 는 서명 키를 갖고 있어 비용이 거의 없고,
 * 이 경로가 내부 키만 있으면 게이트웨이를 우회해서도 닿기 때문이다. 우회 호출이 임의의
 * `jti` 를 들고 오면 남의 세션 기록을 오염시킬 수 있다 — 검증이 그걸 막는다.
 *
 * 헤더의 사용자와 토큰의 subject 가 다르면 거부한다. 둘이 어긋나는 정상 경로는 없다.
 */
@Component
public class RefreshTokenReader {

    private static final String BEARER = "Bearer ";
    private static final String CLAIM_TYPE = "typ";
    private static final String TYPE_REFRESH = "refresh";

    private final JwtKeyProvider keyProvider;

    public RefreshTokenReader(JwtKeyProvider keyProvider) {
        this.keyProvider = keyProvider;
    }

    /**
     * @param authorization `Authorization` 헤더 원본. 없거나 형식이 다르면 예외다
     * @param expectedUserId 게이트웨이가 넘긴 사용자 식별자
     */
    public RefreshToken read(String authorization, Long expectedUserId) {
        if (authorization == null || !authorization.startsWith(BEARER)) {
            throw new InvalidRefreshTokenException("재발급 토큰이 없다");
        }
        try {
            SignedJWT jwt = SignedJWT.parse(authorization.substring(BEARER.length()).trim());
            if (!jwt.verify(new RSASSAVerifier(keyProvider.signingKey().toRSAPublicKey()))) {
                throw new InvalidRefreshTokenException("서명이 맞지 않는다");
            }
            JWTClaimsSet claims = jwt.getJWTClaimsSet();

            if (!TYPE_REFRESH.equals(claims.getStringClaim(CLAIM_TYPE))) {
                throw new InvalidRefreshTokenException("재발급 토큰이 아니다");
            }
            if (claims.getExpirationTime() == null
                    || claims.getExpirationTime().toInstant().isBefore(Instant.now())) {
                throw new InvalidRefreshTokenException("만료된 토큰이다");
            }
            if (claims.getJWTID() == null || claims.getJWTID().isBlank()) {
                throw new InvalidRefreshTokenException("jti 가 없다");
            }
            if (expectedUserId == null || !expectedUserId.toString().equals(claims.getSubject())) {
                throw new InvalidRefreshTokenException("헤더의 사용자와 토큰의 주체가 다르다");
            }
            return new RefreshToken(expectedUserId, claims.getJWTID(),
                    claims.getExpirationTime().toInstant());
        } catch (InvalidRefreshTokenException e) {
            throw e;
        } catch (Exception e) {
            throw new InvalidRefreshTokenException("토큰을 읽을 수 없다: " + e.getMessage());
        }
    }

    public record RefreshToken(Long userId, String jti, Instant expiresAt) {
    }

    /** 사유는 로그에만 남긴다. 응답에서는 구분하지 않는다 — 구분이 곧 단서가 된다. */
    public static class InvalidRefreshTokenException extends RuntimeException {
        public InvalidRefreshTokenException(String message) {
            super(message);
        }
    }
}

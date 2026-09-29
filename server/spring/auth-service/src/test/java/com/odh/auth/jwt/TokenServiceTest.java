package com.odh.auth.jwt;

import com.nimbusds.jwt.SignedJWT;
import com.odh.auth.domain.SystemRole;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 액세스·재발급 토큰 발급.
 *
 * 개인키를 비워 두면 JwtKeyProvider 가 임시 RSA 키를 만들므로 별도 키 설정 없이
 * 순수 단위 테스트로 서명·클레임을 검증할 수 있다.
 */
class TokenServiceTest {

    private TokenService tokenService;
    private JwtKeyProvider keyProvider;

    @BeforeEach
    void setUp() {
        JwtProperties properties = new JwtProperties(null, "org-decision-history",
                Duration.ofMinutes(30), Duration.ofDays(14), Duration.ofSeconds(10));
        keyProvider = new JwtKeyProvider(properties);
        tokenService = new TokenService(keyProvider, properties);
    }

    @Test
    void accessToken_carriesSubjectEmailAndRoles() throws Exception {
        String token = tokenService.issueAccessToken(42L, "admin@company.com",
                List.of(SystemRole.SYSTEM_ADMIN, SystemRole.TEAM_ADMIN));

        SignedJWT jwt = SignedJWT.parse(token);
        assertThat(jwt.verify(new com.nimbusds.jose.crypto.RSASSAVerifier(keyProvider.signingKey().toRSAPublicKey())))
                .isTrue();

        var claims = jwt.getJWTClaimsSet();
        assertThat(claims.getSubject()).isEqualTo("42");
        assertThat(claims.getStringClaim("email")).isEqualTo("admin@company.com");
        assertThat(claims.getStringListClaim("roles")).containsExactly("SYSTEM_ADMIN", "TEAM_ADMIN");
        assertThat(claims.getClaim("typ")).isNull();
        assertThat(claims.getExpirationTime()).isAfter(new java.util.Date());
    }

    @Test
    void refreshToken_isMarkedWithRefreshType() throws Exception {
        String token = tokenService.issueRefreshToken(7L);

        var claims = SignedJWT.parse(token).getJWTClaimsSet();
        assertThat(claims.getSubject()).isEqualTo("7");
        assertThat(claims.getStringClaim("typ")).isEqualTo("refresh");
    }

    @Test
    void accessAndRefreshTokens_useDifferentExpiryHorizons() throws Exception {
        String access = tokenService.issueAccessToken(1L, "a@b.com", List.of(SystemRole.MEMBER));
        String refresh = tokenService.issueRefreshToken(1L);

        var accessExp = SignedJWT.parse(access).getJWTClaimsSet().getExpirationTime();
        var refreshExp = SignedJWT.parse(refresh).getJWTClaimsSet().getExpirationTime();

        // 액세스 토큰은 30분, 재발급 토큰은 14일 — 재발급 토큰이 훨씬 나중에 만료돼야 한다
        assertThat(refreshExp).isAfter(accessExp);
    }

    @Test
    void accessTtlSeconds_matchesConfiguredDuration() {
        assertThat(tokenService.accessTtlSeconds()).isEqualTo(Duration.ofMinutes(30).getSeconds());
    }
}

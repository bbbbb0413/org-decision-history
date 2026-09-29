package com.odh.auth.jwt;

import com.odh.auth.domain.SystemRole;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 재발급 토큰 읽기.
 *
 * 게이트웨이가 이미 검증한 토큰이지만, 이 경로는 내부 키만 있으면 게이트웨이를 우회해서도
 * 닿는다. 우회 호출이 임의의 `jti` 나 남의 subject 를 들고 오는 것을 여기서 막는지 본다.
 */
class RefreshTokenReaderTest {

    private TokenService tokenService;
    private RefreshTokenReader reader;
    private JwtKeyProvider keyProvider;

    @BeforeEach
    void setUp() {
        JwtProperties properties = new JwtProperties(null, "org-decision-history",
                Duration.ofMinutes(30), Duration.ofDays(14), Duration.ofSeconds(10));
        keyProvider = new JwtKeyProvider(properties);
        tokenService = new TokenService(keyProvider, properties);
        reader = new RefreshTokenReader(keyProvider);
    }

    @Test
    void readsJtiAndExpiry() {
        String token = tokenService.issueRefreshToken(42L);

        var parsed = reader.read("Bearer " + token, 42L);

        assertThat(parsed.userId()).isEqualTo(42L);
        assertThat(parsed.jti()).isNotBlank();
        assertThat(parsed.expiresAt()).isAfter(java.time.Instant.now());
    }

    @Test
    void everyTokenGetsItsOwnJti() {
        // 회전은 jti 로 토큰을 구분한다. 같은 사용자의 두 토큰이 같은 jti 를 받으면
        // 정상 재발급이 재사용으로 오판된다.
        String first = reader.read("Bearer " + tokenService.issueRefreshToken(7L), 7L).jti();
        String second = reader.read("Bearer " + tokenService.issueRefreshToken(7L), 7L).jti();

        assertThat(first).isNotEqualTo(second);
    }

    @Test
    void rejectsAccessToken() {
        String access = tokenService.issueAccessToken(42L, "a@b.c", List.of(SystemRole.MEMBER));

        assertThatThrownBy(() -> reader.read("Bearer " + access, 42L))
                .isInstanceOf(RefreshTokenReader.InvalidRefreshTokenException.class);
    }

    @Test
    void rejectsSubjectMismatch() {
        String token = tokenService.issueRefreshToken(42L);

        // 게이트웨이를 우회해 남의 사용자 헤더를 붙인 호출
        assertThatThrownBy(() -> reader.read("Bearer " + token, 99L))
                .isInstanceOf(RefreshTokenReader.InvalidRefreshTokenException.class);
    }

    @Test
    void rejectsTokenSignedByAnotherKey() {
        JwtProperties other = new JwtProperties(null, "org-decision-history",
                Duration.ofMinutes(30), Duration.ofDays(14), Duration.ofSeconds(10));
        String foreign = new TokenService(new JwtKeyProvider(other), other).issueRefreshToken(42L);

        assertThatThrownBy(() -> reader.read("Bearer " + foreign, 42L))
                .isInstanceOf(RefreshTokenReader.InvalidRefreshTokenException.class);
    }

    @Test
    void rejectsMissingOrMalformedHeader() {
        assertThatThrownBy(() -> reader.read(null, 42L))
                .isInstanceOf(RefreshTokenReader.InvalidRefreshTokenException.class);
        assertThatThrownBy(() -> reader.read("nonsense", 42L))
                .isInstanceOf(RefreshTokenReader.InvalidRefreshTokenException.class);
        assertThatThrownBy(() -> reader.read("Bearer not.a.jwt", 42L))
                .isInstanceOf(RefreshTokenReader.InvalidRefreshTokenException.class);
    }
}

package com.odh.auth.jwt;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

/**
 * @param privateKey PKCS#8 PEM 본문. 비어 있으면 기동 시 임시 키를 만든다 (로컬 개발 전용)
 * @param issuer     토큰 발급자
 * @param accessTtl  액세스 토큰 유효 시간
 * @param refreshTtl 재발급 토큰 유효 시간
 * @param refreshReuseGrace 재발급 토큰을 처음 쓴 뒤 이 시간 안의 재사용은 탭 동시 재발급으로
 *                          보고 통과시킨다. 0 이면 엄격 모드다 — 경합까지 재사용으로 판정해
 *                          세션을 끊는다. 자세한 근거는 {@link RefreshTokenRotation}
 */
@ConfigurationProperties(prefix = "auth.jwt")
public record JwtProperties(
        String privateKey,
        String issuer,
        Duration accessTtl,
        Duration refreshTtl,
        Duration refreshReuseGrace
) {
    public JwtProperties {
        if (issuer == null || issuer.isBlank()) {
            issuer = "org-decision-history";
        }
        if (accessTtl == null) {
            accessTtl = Duration.ofMinutes(30);
        }
        if (refreshTtl == null) {
            refreshTtl = Duration.ofDays(14);
        }
        if (refreshReuseGrace == null || refreshReuseGrace.isNegative()) {
            refreshReuseGrace = Duration.ofSeconds(10);
        }
    }
}

package com.odh.auth.jwt;

import com.odh.common.TokenRevocation;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Instant;

/**
 * "이 사용자의 이 시각 이전 발급 토큰은 전부 무효" 워터마크를 찍는다({@link TokenRevocation}).
 *
 * 로그아웃 말고도 세션을 즉시 끊어야 하는 조작이 여럿이라(비밀번호 변경·재발급, 계정 비활성화)
 * 한 군데로 모았다. 안 그러면 Redis 키 규약과 TTL 계산이 네 군데로 흩어진다.
 */
@Component
public class TokenRevoker {

    private final StringRedisTemplate redis;
    private final JwtProperties jwtProperties;

    public TokenRevoker(StringRedisTemplate redis, JwtProperties jwtProperties) {
        this.redis = redis;
        this.jwtProperties = jwtProperties;
    }

    /**
     * 지금까지 발급된 액세스·재발급 토큰을 전부 무효화한다. 기기별 선택이 아니라 모든 세션이 끝난다.
     * TTL 은 재발급 토큰 수명과 같다 — 그보다 오래 두면 이미 만료된 토큰을 막으려고 키만 남는다.
     */
    public void revokeAll(Long userId) {
        redis.opsForValue().set(
                TokenRevocation.watermarkKey(String.valueOf(userId)),
                String.valueOf(Instant.now().getEpochSecond()),
                jwtProperties.refreshTtl());
    }
}

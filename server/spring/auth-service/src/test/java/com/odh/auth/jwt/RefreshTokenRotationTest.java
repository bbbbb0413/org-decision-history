package com.odh.auth.jwt;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 재발급 토큰 회전·재사용 감지.
 *
 * Redis 는 가짜 `ValueOperations` 로 대신한다 — 검증하려는 것은 `SETNX` 성공/실패에 따른
 * 판정이고, Redis 자체의 동작이 아니다.
 */
class RefreshTokenRotationTest {

    private Map<String, String> store;
    private RefreshTokenRotation rotation;

    private void setUpWithGrace(Duration grace) {
        store = new HashMap<>();

        @SuppressWarnings("unchecked")
        ValueOperations<String, String> ops = mock(ValueOperations.class);
        when(ops.setIfAbsent(anyString(), anyString(), any(Duration.class)))
                .thenAnswer(invocation -> {
                    String key = invocation.getArgument(0);
                    if (store.containsKey(key)) {
                        return false;
                    }
                    store.put(key, invocation.getArgument(1));
                    return true;
                });
        when(ops.get(anyString())).thenAnswer(invocation -> store.get(invocation.getArgument(0)));

        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        when(redis.opsForValue()).thenReturn(ops);

        rotation = new RefreshTokenRotation(redis,
                new JwtProperties(null, "org-decision-history",
                        Duration.ofMinutes(30), Duration.ofDays(14), grace));
    }

    @BeforeEach
    void setUp() {
        setUpWithGrace(Duration.ofSeconds(10));
    }

    private Instant later() {
        return Instant.now().plus(Duration.ofDays(14));
    }

    @Test
    void firstUse_isAccepted() {
        assertThat(rotation.claim("jti-1", later())).isEqualTo(RefreshTokenRotation.Outcome.FIRST_USE);
    }

    @Test
    void secondUseWithinGrace_isTreatedAsConcurrentRefresh() {
        rotation.claim("jti-1", later());

        // 같은 초에 다시 오는 것이 탭 여러 개의 동시 재발급이다. 끊으면 정상 사용자가 로그아웃된다.
        assertThat(rotation.claim("jti-1", later())).isEqualTo(RefreshTokenRotation.Outcome.GRACE_REPLAY);
    }

    @Test
    void secondUseAfterGrace_isReuse() {
        setUpWithGrace(Duration.ZERO);
        rotation.claim("jti-1", later());
        // 유예 0 이면 같은 초의 재사용도 사고로 본다(엄격 모드). 경계가 실제로 먹는지 확인한다.
        store.put(RefreshTokenRotation.usedKey("jti-1"),
                String.valueOf(Instant.now().minusSeconds(1).getEpochSecond()));

        assertThat(rotation.claim("jti-1", later())).isEqualTo(RefreshTokenRotation.Outcome.REUSE_DETECTED);
    }

    @Test
    void useLongAfterFirstUse_isReuse() {
        rotation.claim("jti-1", later());
        store.put(RefreshTokenRotation.usedKey("jti-1"),
                String.valueOf(Instant.now().minus(Duration.ofHours(1)).getEpochSecond()));

        assertThat(rotation.claim("jti-1", later())).isEqualTo(RefreshTokenRotation.Outcome.REUSE_DETECTED);
    }

    @Test
    void differentTokens_doNotInterfere() {
        assertThat(rotation.claim("jti-1", later())).isEqualTo(RefreshTokenRotation.Outcome.FIRST_USE);
        assertThat(rotation.claim("jti-2", later())).isEqualTo(RefreshTokenRotation.Outcome.FIRST_USE);
    }

    @Test
    void expiredToken_isNotRecorded() {
        // 만료 토큰은 게이트웨이가 이미 거른다. 기록을 남겨도 TTL 을 계산할 수 없다.
        assertThat(rotation.claim("jti-old", Instant.now().minusSeconds(5)))
                .isEqualTo(RefreshTokenRotation.Outcome.FIRST_USE);
        assertThat(store).isEmpty();
    }

    @Test
    void missingRecordOnSecondUse_isTreatedAsReuse() {
        // SETNX 는 실패했는데 값이 없다(TTL 경계). 언제 쓰였는지 모르면 통과시키는 쪽으로 기울지 않는다.
        rotation.claim("jti-1", later());
        store.remove(RefreshTokenRotation.usedKey("jti-1"));
        store.put(RefreshTokenRotation.usedKey("jti-1"), "not-a-number");

        assertThat(rotation.claim("jti-1", later())).isEqualTo(RefreshTokenRotation.Outcome.REUSE_DETECTED);
    }

    @Test
    void graceDefaultsToTenSeconds_whenUnset() {
        JwtProperties properties = new JwtProperties(null, null, null, null, null);
        assertThat(properties.refreshReuseGrace()).isEqualTo(Duration.ofSeconds(10));
    }

    @Test
    void negativeGrace_fallsBackToDefault() {
        JwtProperties properties = new JwtProperties(null, null, null, null, Duration.ofSeconds(-5));
        assertThat(properties.refreshReuseGrace()).isEqualTo(Duration.ofSeconds(10));
    }
}

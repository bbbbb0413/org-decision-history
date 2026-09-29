package com.odh.auth.jwt;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;

/**
 * 재발급 토큰 회전과 재사용 감지.
 *
 * 회전이 없으면 같은 재발급 토큰을 몇 번이고 다시 쓸 수 있다. 탈취되면 만료(14일)까지
 * 무제한으로 액세스 토큰을 뽑을 수 있고, 정상 사용자가 계속 쓰고 있어도 그 사실이
 * 어디에도 드러나지 않는다.
 *
 * <h2>쓴 토큰을 낱개로 기록한다</h2>
 *
 * 로그아웃은 사용자별 워터마크로 처리한다({@link com.odh.common.TokenRevocation}) —
 * 발급한 토큰 목록을 들고 있지 않아도 "이 시각 이전 전부 무효"를 만들 수 있기 때문이다.
 * 하지만 재사용 감지는 "이 토큰이 전에 쓰였나"를 물어야 하므로 낱개 기록이 필요하다.
 * 그래서 토큰 전체가 아니라 `jti` 만 남긴다({@link TokenService} 가 발급 시 UUID 로 채운다).
 * TTL 을 그 토큰의 남은 수명으로 두면 만료된 토큰의 기록은 알아서 사라진다 — 따로 청소하지 않는다.
 *
 * <h2>유예 구간을 두는 이유</h2>
 *
 * 탭을 여러 개 띄운 사용자는 액세스 토큰이 만료되는 순간 **같은 재발급 토큰으로 동시에**
 * 재발급을 시도한다. 회전만 넣고 유예가 없으면 한쪽이 재사용으로 판정돼 로그아웃된다 —
 * 공격이 아니라 정상 사용인데 세션이 끊긴다.
 *
 * 그래서 첫 사용 직후 짧은 구간 안의 재사용은 경합으로 보고 통과시킨다. 대가는 분명하다:
 * <b>유예 구간 안에서는 탈취된 토큰도 통과한다.</b> 그 구간을 짧게 두는 것으로 균형을 잡고,
 * 0 으로 설정하면 엄격 모드가 된다(경합 로그아웃을 감수하는 선택).
 *
 * 유예 밖 재사용은 사고로 본다. 그때는 그 토큰만 막는 것으로 부족하다 — 이미 누가 복제본을
 * 들고 있다는 뜻이므로 {@link TokenRevoker#revokeAll(Long)} 로 그 사용자의 세션을 전부 끝낸다
 * (OAuth 2.0 Security BCP §4.13.2 의 재사용 감지 권고).
 */
@Component
public class RefreshTokenRotation {

    /** 회전 결과. 호출자가 이걸 보고 발급할지 세션을 끊을지 정한다. */
    public enum Outcome {
        /** 처음 쓰인 토큰이다. 정상 회전. */
        FIRST_USE,
        /** 유예 구간 안의 재사용이다. 탭 동시 재발급으로 보고 통과시킨다. */
        GRACE_REPLAY,
        /** 유예 구간 밖의 재사용이다. 세션을 전부 끊어야 한다. */
        REUSE_DETECTED
    }

    private static final String KEY_PREFIX = "auth:refresh-used:";

    private final StringRedisTemplate redis;
    private final JwtProperties properties;

    public RefreshTokenRotation(StringRedisTemplate redis, JwtProperties properties) {
        this.redis = redis;
        this.properties = properties;
    }

    static String usedKey(String jti) {
        return KEY_PREFIX + jti;
    }

    /**
     * 이 재발급 토큰을 "썼다"고 기록하고 결과를 돌려준다.
     *
     * @param jti       재발급 토큰의 jti
     * @param expiresAt 그 토큰의 만료 시각. 기록 TTL 로 쓴다
     */
    public Outcome claim(String jti, Instant expiresAt) {
        Instant now = Instant.now();
        Duration ttl = Duration.between(now, expiresAt);
        if (ttl.isNegative() || ttl.isZero()) {
            // 만료된 토큰은 게이트웨이가 이미 걸렀다. 여기까지 왔으면 기록해도 의미가 없으니
            // 흔적을 남기지 않고 첫 사용으로 취급한다 — 뒤에서 만료를 다시 확인한다.
            return Outcome.FIRST_USE;
        }

        String key = usedKey(jti);
        Boolean first = redis.opsForValue().setIfAbsent(key, String.valueOf(now.getEpochSecond()), ttl);
        if (Boolean.TRUE.equals(first)) {
            return Outcome.FIRST_USE;
        }

        // 이미 쓰인 토큰이다. 언제 쓰였는지 보고 경합인지 사고인지 가린다.
        // 기록이 방금 사라졌다면(TTL 경계) 첫 사용으로 되돌리지 않고 보수적으로 사고로 본다.
        Instant firstUsedAt = readFirstUsedAt(key);
        if (firstUsedAt == null) {
            return Outcome.REUSE_DETECTED;
        }
        Duration grace = properties.refreshReuseGrace();
        boolean withinGrace = !now.isAfter(firstUsedAt.plus(grace));
        return withinGrace ? Outcome.GRACE_REPLAY : Outcome.REUSE_DETECTED;
    }

    private Instant readFirstUsedAt(String key) {
        String value = redis.opsForValue().get(key);
        if (value == null) {
            return null;
        }
        try {
            return Instant.ofEpochSecond(Long.parseLong(value.trim()));
        } catch (NumberFormatException e) {
            // 값이 깨졌으면 언제 쓰였는지 알 수 없다. 통과시키는 쪽으로 기울지 않는다.
            return null;
        }
    }
}

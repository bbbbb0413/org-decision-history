package com.odh.gateway.filter;

import com.nimbusds.jose.KeySourceException;
import com.nimbusds.jose.jwk.JWK;
import com.nimbusds.jose.jwk.JWKSelector;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.source.JWKSetCacheRefreshEvaluator;
import com.nimbusds.jose.jwk.source.JWKSetSource;
import com.nimbusds.jose.jwk.source.JWKSetUnavailableException;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.jwk.source.OutageTolerantJWKSetSource;
import com.nimbusds.jose.jwk.source.RefreshAheadCachingJWKSetSource;
import com.nimbusds.jose.jwk.source.RetryingJWKSetSource;
import com.nimbusds.jose.jwk.source.URLBasedJWKSetSource;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jose.util.DefaultResourceRetriever;
import com.nimbusds.jose.util.events.Event;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.Closeable;
import java.io.IOException;
import java.net.URL;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/**
 * auth-service JWKS 를 들고 있는 키 소스. Nimbus {@code JWKSourceBuilder} 기본 체인 대신 직접 조립한다.
 *
 * 목적은 응답을 정확히 가르는 것이다: "이 토큰의 키를 모른다"(→ 401)와 "키를 확인하지 못했다"(→ 503).
 * 기본 체인의 재조회 제한기는 제한에 걸리면 {@code RateLimitReachedException}(키 조회 실패)을 던져,
 * 모르는 kid 토큰이 503 이 되거나(제한기를 401 로 분류하면) auth-service 가 내려간 채 기동했을 때
 * 정상 사용자가 401 → 로그아웃된다. 그래서
 * - 모르는 kid 로 다시 받아오기만 {@code unknownKidRefreshInterval} 에 한 번으로 묶는다(JWKS 를 두드리지
 *   못하게). 묶인 동안은 받지 않고 "맞는 키 없음"으로 돌려준다 → 401.
 * - 정상 조회(기동·만료·미리 갱신·스케줄)에는 제한기 대신 실패 백오프만 둔다: 조회가 실패하면
 *   {@code failureBackoff} 동안은 auth-service 에 다시 묻지 않고 바로 실패한다(→ 503). auth-service 가
 *   내려간 채 기동했을 때 요청마다 조회를 다시 시도해 응답이 늦어지고 복구 중인 auth-service 를 두드리지 않게.
 *
 * 체인(안쪽부터): URL 조회 → 한 번 재시도 → 실패 백오프 → 장애 허용(조회 실패 시 마지막 키) → 미리 갱신
 * 캐시(스케줄). 장애 허용이 백오프보다 바깥이라, 받아 둔 키가 있으면 백오프 중에도 그 키로 검증한다.
 *
 * 참고: 모르는 kid 재조회가 정상 갱신을 막지는 않는다 — 강제 재조회가 성공할 때마다 캐시 만료도 함께
 * 밀려, 그 트래픽이 이어지는 동안 캐시가 만료되지 않는다.
 */
final class GatewayKeySource implements JWKSource<SecurityContext>, Closeable {

    private static final Logger log = LoggerFactory.getLogger(GatewayKeySource.class);

    private final JWKSetSource<SecurityContext> source;
    private final long unknownKidRefreshIntervalMillis;
    private final AtomicLong nextUnknownKidRefresh = new AtomicLong();

    GatewayKeySource(URL jwksUrl, JwtVerifier.KeySourceTiming timing, Consumer<Event<?, SecurityContext>> events) {
        int httpTimeout = (int) timing.httpTimeout().toMillis();
        JWKSetSource<SecurityContext> chain = new URLBasedJWKSetSource<>(jwksUrl,
                new DefaultResourceRetriever(httpTimeout, httpTimeout, 50 * 1024));
        chain = new RetryingJWKSetSource<>(chain, events::accept);
        chain = new FailureBackoffJWKSetSource(chain, timing.failureBackoff().toMillis());
        chain = new OutageTolerantJWKSetSource<>(chain, timing.outageTtl().toMillis(), events::accept);
        this.source = new RefreshAheadCachingJWKSetSource<>(chain,
                timing.cacheTtl().toMillis(), timing.refreshWait().toMillis(), timing.refreshAhead().toMillis(),
                Executors.newSingleThreadExecutor(daemon("jwks-refresh")), true,
                events::accept,
                Executors.newSingleThreadScheduledExecutor(daemon("jwks-refresh-schedule")), true);
        this.unknownKidRefreshIntervalMillis = timing.unknownKidRefreshInterval().toMillis();
    }

    @Override
    public List<JWK> get(JWKSelector selector, SecurityContext context) throws KeySourceException {
        long now = System.currentTimeMillis();
        JWKSet jwkSet = source.getJWKSet(JWKSetCacheRefreshEvaluator.noRefresh(), now, context);
        List<JWK> selected = selector.select(jwkSet);
        if (!selected.isEmpty()) {
            return selected;
        }
        if (!tryStartUnknownKidRefresh(now)) {
            log.debug("모르는 kid — 최근에 다시 받아왔으므로 이번에는 받지 않는다");
            return selected;
        }
        // 다른 요청이 이미 새 키를 받아 왔으면 다시 받지 않는다(참조 비교)
        JWKSet recent = source.getJWKSet(JWKSetCacheRefreshEvaluator.referenceComparison(jwkSet), now, context);
        return selector.select(recent);
    }

    private boolean tryStartUnknownKidRefresh(long now) {
        long next = nextUnknownKidRefresh.get();
        return next <= now && nextUnknownKidRefresh.compareAndSet(next, now + unknownKidRefreshIntervalMillis);
    }

    @Override
    public void close() throws IOException {
        source.close();
    }

    private static ThreadFactory daemon(String name) {
        return r -> {
            Thread t = new Thread(r, name);
            t.setDaemon(true);
            return t;
        };
    }

    /** 조회가 실패하면 {@code backoffMillis} 동안 다시 묻지 않고 바로 실패한다. */
    static final class FailureBackoffJWKSetSource implements JWKSetSource<SecurityContext> {

        private final JWKSetSource<SecurityContext> delegate;
        private final long backoffMillis;
        private volatile long retryAfter;

        FailureBackoffJWKSetSource(JWKSetSource<SecurityContext> delegate, long backoffMillis) {
            this.delegate = delegate;
            this.backoffMillis = backoffMillis;
        }

        @Override
        public JWKSet getJWKSet(JWKSetCacheRefreshEvaluator refreshEvaluator, long currentTime, SecurityContext context)
                throws KeySourceException {
            if (currentTime < retryAfter) {
                throw new JWKSetUnavailableException("최근 JWKS 조회가 실패해 " + (retryAfter - currentTime) + "ms 동안 다시 묻지 않는다");
            }
            try {
                return delegate.getJWKSet(refreshEvaluator, currentTime, context);
            } catch (KeySourceException e) {
                retryAfter = currentTime + backoffMillis;
                throw e;
            }
        }

        @Override
        public void close() throws IOException {
            delegate.close();
        }
    }
}

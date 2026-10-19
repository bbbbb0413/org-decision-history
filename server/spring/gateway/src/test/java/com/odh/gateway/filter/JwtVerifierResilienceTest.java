package com.odh.gateway.filter;

import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.KeyUse;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.ReactiveValueOperations;
import reactor.core.publisher.Mono;

import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URL;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 키 조회(JWKS)가 흔들려도 정상 토큰 검증이 멈추지 않는지.
 * 응답 코드·지연·키 목록을 바꿀 수 있는 JWKS 서버로 auth-service 의 장애를 흉내 낸다.
 */
class JwtVerifierResilienceTest {

    /** 테스트용으로 줄인 시간. 스케줄 갱신은 걸리지 않는다(만료 − 미리 갱신 − 대기 = 0) — 요청이 갱신을 이끈다 */
    private static final JwtVerifier.KeySourceTiming FAST = new JwtVerifier.KeySourceTiming(
            Duration.ofMillis(1000), Duration.ofMillis(200), Duration.ofMillis(800),
            Duration.ofSeconds(10), Duration.ofSeconds(60), Duration.ofMillis(300), Duration.ofMillis(200));

    private HttpServer server;
    private final AtomicInteger status = new AtomicInteger(200);
    private final AtomicLong delayMillis = new AtomicLong();
    private final AtomicInteger hits = new AtomicInteger();
    private final AtomicReference<JWKSet> served = new AtomicReference<>();
    private URL jwksUrl;
    private RSAKey keyA;

    @BeforeEach
    void startServer() throws Exception {
        keyA = generateKey("key-a");
        served.set(new JWKSet(keyA.toPublicJWK()));
        server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        server.setExecutor(java.util.concurrent.Executors.newCachedThreadPool());
        server.createContext("/jwks.json", exchange -> {
            hits.incrementAndGet();
            try {
                Thread.sleep(delayMillis.get());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            byte[] body = status.get() == 200
                    ? new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsBytes(served.get().toJSONObject())
                    : "{}".getBytes();
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status.get(), body.length);
            try (var os = exchange.getResponseBody()) {
                os.write(body);
            }
        });
        server.start();
        jwksUrl = URI.create("http://localhost:" + server.getAddress().getPort() + "/jwks.json").toURL();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    @Test
    void keysStayUsableWhileAuthServiceIsDown() throws Exception {
        JwtVerifier verifier = new JwtVerifier(jwksUrl, FAST, noRevocations());
        assertThat(verifier.verify(token(keyA)).block()).isNotNull();

        status.set(500);              // auth-service 가 응답하지 않는다
        Thread.sleep(1300);           // 캐시 수명(1s)을 넘긴다

        // 캐시는 만료됐고 조회는 실패하지만 마지막으로 받은 키를 쓴다
        assertThat(verifier.verify(token(keyA)).block(Duration.ofSeconds(5))).isNotNull();
        verifier.destroy();
    }

    @Test
    void coldStartWithAuthServiceDownIsAnInfrastructureFailureNotABadToken() throws Exception {
        status.set(500);
        JwtVerifier verifier = new JwtVerifier(jwksUrl, FAST, noRevocations());

        assertThatThrownBy(() -> verifier.verify(token(keyA)).block(Duration.ofSeconds(5)))
                .satisfies(e -> assertThat(AuthenticationFilter.isMalformedOrForged(unwrap(e)))
                        .as("키를 한 번도 받지 못한 것은 토큰이 틀린 게 아니다 → 503: %s", unwrap(e)).isFalse());
        verifier.destroy();
    }

    /** 모르는 kid 는 계속 401(토큰을 모른다)이고, JWKS 를 두드리지 않으며, 그 사이 정상 토큰은 계속 통과한다. */
    @Test
    void unknownKidFloodIsRejectedAs401WithoutHammeringJwks() throws Exception {
        JwtVerifier verifier = new JwtVerifier(jwksUrl, FAST, noRevocations());
        assertThat(verifier.verify(token(keyA)).block()).isNotNull();
        RSAKey unknown = generateKey("rotated-away");

        long until = System.currentTimeMillis() + 1600;   // 캐시 수명(1s)을 넘겨 계속 섞어 보낸다
        int ok = 0;
        while (System.currentTimeMillis() < until) {
            for (int i = 0; i < 5; i++) {
                assertThatThrownBy(() -> verifier.verify(token(unknown)).block())
                        .satisfies(e -> assertThat(AuthenticationFilter.isMalformedOrForged(unwrap(e))).as("%s", unwrap(e)).isTrue());
            }
            assertThat(verifier.verify(token(keyA)).block(Duration.ofSeconds(5))).isNotNull();
            ok++;
            Thread.sleep(50);
        }

        assertThat(ok).isGreaterThan(10);
        // 모르는 kid 로 다시 받아오기는 간격(10s)당 한 번. 나머지는 첫 조회 + 만료 갱신뿐이다
        assertThat(hits.get()).isLessThanOrEqualTo(5);
        verifier.destroy();
    }

    /** 기동 직후 auth-service 가 내려가 있으면, 실패 백오프 동안은 다시 묻지 않고 바로 503 쪽 실패를 낸다. */
    @Test
    void failedFetchBacksOffInsteadOfRetryingOnEveryRequest() throws Exception {
        status.set(500);
        JwtVerifier.KeySourceTiming backoff = new JwtVerifier.KeySourceTiming(
                Duration.ofSeconds(5), Duration.ofMillis(200), Duration.ofSeconds(2),
                Duration.ofSeconds(10), Duration.ofSeconds(60), Duration.ofMillis(300), Duration.ofSeconds(2));
        JwtVerifier verifier = new JwtVerifier(jwksUrl, backoff, noRevocations());

        assertThatThrownBy(() -> verifier.verify(token(keyA)).block(Duration.ofSeconds(5)));
        int afterFirst = hits.get();  // 조회 1번 + 재시도 1번
        for (int i = 0; i < 5; i++) {
            long start = System.nanoTime();
            assertThatThrownBy(() -> verifier.verify(token(keyA)).block(Duration.ofSeconds(5)))
                    .satisfies(e -> assertThat(AuthenticationFilter.isMalformedOrForged(unwrap(e))).isFalse());
            assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(500);
        }
        assertThat(hits.get()).as("백오프 동안 auth-service 에 다시 묻지 않는다").isEqualTo(afterFirst);

        status.set(200);              // auth-service 복구
        Thread.sleep(2100);           // 백오프가 지나면
        assertThat(verifier.verify(token(keyA)).block(Duration.ofSeconds(5))).isNotNull();
        verifier.destroy();
    }

    @Test
    void refreshesInTheBackgroundBeforeExpiry() throws Exception {
        // 만료 3s − 미리 갱신 1s − 대기 0.5s = 1.5s 뒤 스케줄 갱신
        JwtVerifier.KeySourceTiming scheduled = new JwtVerifier.KeySourceTiming(
                Duration.ofSeconds(3), Duration.ofSeconds(1), Duration.ofMillis(500),
                Duration.ofSeconds(10), Duration.ofSeconds(60), Duration.ofMillis(300), Duration.ofMillis(200));
        JwtVerifier verifier = new JwtVerifier(jwksUrl, scheduled, noRevocations());
        assertThat(verifier.verify(token(keyA)).block()).isNotNull();
        assertThat(hits.get()).isEqualTo(1);

        Thread.sleep(2200);           // 요청 없이 기다린다

        assertThat(hits.get()).as("요청이 없어도 만료 전에 갱신한다").isGreaterThanOrEqualTo(2);
        verifier.destroy();
    }

    /** 키 조회는 블로킹이다. 구독하는 스레드(실제로는 Netty 이벤트 루프)를 붙잡지 않아야 한다. */
    @Test
    void verificationDoesNotBlockTheSubscribingThread() throws Exception {
        delayMillis.set(800);         // 기동 직후 첫 조회가 느리다
        JwtVerifier.KeySourceTiming slowHttp = new JwtVerifier.KeySourceTiming(
                Duration.ofSeconds(5), Duration.ofMillis(200), Duration.ofSeconds(2),
                Duration.ofSeconds(10), Duration.ofSeconds(60), Duration.ofSeconds(2), Duration.ofMillis(200));
        JwtVerifier verifier = new JwtVerifier(jwksUrl, slowHttp, noRevocations());
        CompletableFuture<JwtVerifier.Claims> result = new CompletableFuture<>();

        long start = System.nanoTime();
        verifier.verify(token(keyA)).subscribe(result::complete, result::completeExceptionally);
        long subscribeMillis = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start);

        assertThat(subscribeMillis).isLessThan(200);
        assertThat(result.get(5, TimeUnit.SECONDS)).isNotNull();
        verifier.destroy();
    }

    /** block() 은 검사 예외를 ReactiveException 으로 감싼다. 필터의 onErrorResume 은 원래 예외를 받는다 */
    private static Throwable unwrap(Throwable e) {
        return reactor.core.Exceptions.unwrap(e);
    }

    @SuppressWarnings("unchecked")
    private static ReactiveStringRedisTemplate noRevocations() {
        ReactiveStringRedisTemplate redis = mock(ReactiveStringRedisTemplate.class);
        ReactiveValueOperations<String, String> valueOps = mock(ReactiveValueOperations.class);
        when(redis.opsForValue()).thenReturn(valueOps);
        when(valueOps.get(anyString())).thenReturn(Mono.empty());
        return redis;
    }

    private static String token(RSAKey key) {
        try {
            Instant now = Instant.now();
            JWTClaimsSet claims = new JWTClaimsSet.Builder()
                    .subject("42")
                    .issueTime(Date.from(now))
                    .expirationTime(Date.from(now.plusSeconds(600)))
                    .claim("roles", List.of("MEMBER"))
                    .build();
            SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256)
                    .keyID(key.getKeyID()).type(JOSEObjectType.JWT).build(), claims);
            jwt.sign(new RSASSASigner(key.toPrivateKey()));
            return jwt.serialize();
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private static RSAKey generateKey(String keyId) throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        KeyPair pair = generator.generateKeyPair();
        return new RSAKey.Builder((RSAPublicKey) pair.getPublic())
                .privateKey((RSAPrivateKey) pair.getPrivate())
                .keyUse(KeyUse.SIGNATURE)
                .keyID(keyId)
                .build();
    }
}

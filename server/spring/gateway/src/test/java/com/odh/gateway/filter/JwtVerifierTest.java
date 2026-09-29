package com.odh.gateway.filter;

import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.KeyUse;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import com.odh.common.TokenRevocation;
import com.odh.gateway.config.GatewayProperties;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.ReactiveValueOperations;
import reactor.core.publisher.Mono;

import java.net.InetSocketAddress;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.RSAPrivateKey;
import java.security.interfaces.RSAPublicKey;
import java.time.Instant;
import java.util.Date;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 게이트웨이가 auth-service 의 JWKS 로 토큰을 검증하는 경로.
 *
 * 실제 auth-service 대신 JDK 내장 HttpServer 로 JWKS 를 흉내 낸다.
 * 서명·만료·서명 위조를 실제로 검증하는 게 이 테스트의 목적이지, 네트워크
 * 계층이 아니라서 굳이 Testcontainers 나 외부 라이브러리를 끌어올 필요가 없다.
 */
class JwtVerifierTest {

    private static HttpServer server;
    private static RSAKey signingKey;
    private static GatewayProperties props;
    private static JwtVerifier verifier;

    @BeforeAll
    static void startJwksServer() throws Exception {
        signingKey = generateKey("test-key");

        server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        server.createContext("/jwks.json", exchange -> {
            // JWKSet.toJSONObject() 는 java.util.Map 을 준다. Map.toString() 은 "=" 구분자를 쓰는
            // 자바 표기라 유효한 JSON 이 아니다 — ObjectMapper 로 실제 JSON 문자열을 만들어야 한다.
            byte[] body = new com.fasterxml.jackson.databind.ObjectMapper()
                    .writeValueAsBytes(new JWKSet(signingKey.toPublicJWK()).toJSONObject());
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (var os = exchange.getResponseBody()) {
                os.write(body);
            }
        });
        server.start();

        props = new GatewayProperties(
                "http://auth", "http://doc", "http://index", "http://chat", "internal-key",
                "http://localhost:" + server.getAddress().getPort() + "/jwks.json", "http://localhost:5173",
                1, 5);
        verifier = new JwtVerifier(props, redisWithNoRevocations());
    }

    @AfterAll
    static void stopJwksServer() {
        server.stop(0);
    }

    /** 로그아웃 워터마크가 아예 없는 상태를 흉내 낸다 — 대부분의 테스트는 revocation 과 무관하다 */
    @SuppressWarnings("unchecked")
    private static ReactiveStringRedisTemplate redisWithNoRevocations() {
        ReactiveStringRedisTemplate redis = mock(ReactiveStringRedisTemplate.class);
        ReactiveValueOperations<String, String> valueOps = mock(ReactiveValueOperations.class);
        when(redis.opsForValue()).thenReturn(valueOps);
        when(valueOps.get(anyString())).thenReturn(Mono.empty());
        return redis;
    }

    @Test
    void validAccessToken_verifiesAndReturnsClaims() {
        String token = sign(signingKey, claims("42", List.of("SYSTEM_ADMIN"), null, minutes(30)));

        JwtVerifier.Claims claims = verifier.verify(token).block();

        assertThat(claims.userId()).isEqualTo("42");
        assertThat(claims.roles()).containsExactly("SYSTEM_ADMIN");
        assertThat(claims.isRefresh()).isFalse();
    }

    @Test
    void refreshToken_isMarkedAsRefresh() {
        String token = sign(signingKey, claims("7", List.of(), "refresh", minutes(30)));

        JwtVerifier.Claims claims = verifier.verify(token).block();

        assertThat(claims.isRefresh()).isTrue();
    }

    @Test
    void expiredToken_isRejected() {
        String token = sign(signingKey, claims("42", List.of("MEMBER"), null, -minutes(1)));

        assertThatThrownBy(() -> verifier.verify(token).block())
                .isInstanceOf(JwtVerifier.TokenExpiredException.class);
    }

    @Test
    void tokenSignedWithUnknownKey_isRejected() throws Exception {
        RSAKey otherKey = generateKey("attacker-key");
        String token = sign(otherKey, claims("42", List.of("SYSTEM_ADMIN"), null, minutes(30)));

        // JWKS 에 없는 kid 이므로 서명 검증 단계에서 거부되어야 한다 — 위조 토큰이 통과하면 인증 전체가 뚫린다
        assertThatThrownBy(() -> verifier.verify(token).block()).isNotNull();
    }

    @Test
    @SuppressWarnings("unchecked")
    void tokenIssuedBeforeLogoutWatermark_isRejected() throws Exception {
        // 지금 발급된 토큰인데, 워터마크는 "이보다 나중"이라 로그아웃 이후로 취급돼야 한다
        String token = sign(signingKey, claims("99", List.of("MEMBER"), null, minutes(30)));

        ReactiveStringRedisTemplate redis = mock(ReactiveStringRedisTemplate.class);
        ReactiveValueOperations<String, String> valueOps = mock(ReactiveValueOperations.class);
        when(redis.opsForValue()).thenReturn(valueOps);
        long revokedAt = Instant.now().plusSeconds(60).getEpochSecond();
        when(valueOps.get(TokenRevocation.watermarkKey("99"))).thenReturn(Mono.just(String.valueOf(revokedAt)));

        JwtVerifier revocationAwareVerifier = new JwtVerifier(props, redis);

        assertThatThrownBy(() -> revocationAwareVerifier.verify(token).block())
                .isInstanceOf(JwtVerifier.TokenRevokedException.class);
    }

    private static long minutes(long minutes) {
        return minutes * 60;
    }

    private static JWTClaimsSet claims(String subject, List<String> roles, String type, long ttlSeconds) {
        Instant now = Instant.now();
        var builder = new JWTClaimsSet.Builder()
                .subject(subject)
                .issuer("org-decision-history")
                .issueTime(Date.from(now))
                .expirationTime(Date.from(now.plusSeconds(ttlSeconds)))
                .claim("roles", roles);
        if (type != null) {
            builder.claim("typ", type);
        }
        return builder.build();
    }

    private static String sign(RSAKey key, JWTClaimsSet claims) {
        try {
            JWSHeader header = new JWSHeader.Builder(JWSAlgorithm.RS256)
                    .keyID(key.getKeyID())
                    .type(JOSEObjectType.JWT)
                    .build();
            SignedJWT jwt = new SignedJWT(header, claims);
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

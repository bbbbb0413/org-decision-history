package com.odh.auth.ops;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.connection.RedisConnection;
import org.springframework.data.redis.core.RedisCallback;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * 저장소·서비스 헬스체크.
 *
 * 실시간으로 확인하고 저장하지 않는다. 구성 요소 하나가 응답하지 않아도 나머지는 계속
 * 확인해 부분 장애에서도 화면이 통째로 비지 않게 한다. 확인 자체에 실패하면 UNKNOWN 으로
 * 표시한다 — 정상(UP)과 혼동하면 장애를 놓친다.
 */
@Service
public class OpsHealthService {

    private static final Logger log = LoggerFactory.getLogger(OpsHealthService.class);

    private final JdbcClient jdbc;
    private final StringRedisTemplate redis;
    private final RestClient http;
    private final String qdrantUrl;
    private final String storageEndpoint;
    private final OpsProperties services;

    public OpsHealthService(JdbcClient jdbc, StringRedisTemplate redis,
                            @Value("${qdrant.url}") String qdrantUrl,
                            @Value("${storage.endpoint}") String storageEndpoint,
                            OpsProperties services) {
        this.jdbc = jdbc;
        this.redis = redis;
        this.qdrantUrl = qdrantUrl;
        this.storageEndpoint = storageEndpoint;
        this.services = services;

        var factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) services.healthTimeout().toMillis());
        factory.setReadTimeout((int) services.healthTimeout().toMillis());
        this.http = RestClient.builder().requestFactory(factory).build();
    }

    public OpsDtos.OpsHealth check() {
        List<OpsDtos.ComponentHealth> storages = List.of(
                timed("PostgreSQL", () -> jdbc.sql("SELECT 1").query(Integer.class).single()),
                timed("Redis", () -> redis.execute((RedisCallback<String>) RedisConnection::ping)),
                timed("Qdrant", () -> http.get().uri(qdrantUrl).retrieve().toBodilessEntity()),
                timed("객체 저장소", () -> http.get().uri(storageEndpoint + "/minio/health/live")
                        .retrieve().toBodilessEntity()));

        List<OpsDtos.ComponentHealth> serviceHealth = List.of(
                // 이 요청에 응답하고 있다는 사실 자체가 auth-service 의 상태다
                new OpsDtos.ComponentHealth("auth", OpsDtos.HealthStatus.UP, 0L),
                timed("gateway", () -> actuatorStatus(services.gatewayUrl())),
                timed("document", () -> actuatorStatus(services.documentUrl())),
                timed("index-worker", () -> actuatorStatus(services.indexUrl())),
                timed("chat", () -> actuatorStatus(services.chatUrl())));

        return new OpsDtos.OpsHealth(storages, serviceHealth, Instant.now());
    }

    private String actuatorStatus(String baseUrl) {
        @SuppressWarnings("unchecked")
        Map<String, Object> body = http.get().uri(baseUrl + "/actuator/health").retrieve().body(Map.class);
        Object status = body == null ? null : body.get("status");
        if (!"UP".equals(status)) {
            throw new IllegalStateException("서비스가 UP 이 아니다: " + status);
        }
        return (String) status;
    }

    /**
     * 호출이 성공하면 걸린 시간과 함께 UP 을, 실패하면 UNKNOWN 을 만든다.
     * Callable 하나로만 받는다 — Runnable 오버로드를 같이 두면 값을 반환하는 람다가
     * 두 함수형 인터페이스 모두와 맞아떨어져 컴파일이 모호해진다.
     */
    private OpsDtos.ComponentHealth timed(String name, java.util.concurrent.Callable<?> check) {
        long start = System.nanoTime();
        try {
            check.call();
            return new OpsDtos.ComponentHealth(name, OpsDtos.HealthStatus.UP, elapsedMs(start));
        } catch (Exception e) {
            log.warn("운영 현황 헬스체크 실패: {} - {}", name, e.getMessage());
            return OpsDtos.ComponentHealth.unknown(name);
        }
    }

    private long elapsedMs(long startNanos) {
        return (System.nanoTime() - startNanos) / 1_000_000;
    }
}

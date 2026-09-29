package com.odh.common.internal;

import com.odh.common.InternalAuth;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.client.RestClient;

import java.util.Map;

/**
 * 감사 기록을 auth-service 에 남긴다.
 *
 * document-service·chat-service 가 각자 들고 있던 동일한 클라이언트를 여기로 합쳤다 —
 * 감사 로그의 소유자를 auth-service 한 곳으로 두어 서비스마다 기록 형식이 갈라지지
 * 않게 한다. 기록 실패가 원래 작업을 막지는 않는다. 다만 조용히 넘기지 않고 로그에
 * 남겨 감사 누락이 눈에 띄게 한다.
 *
 * 내부 호출이라 상관관계 ID는 이 클라이언트가 직접 챙기지 않는다 —
 * {@code common.servlet.ServiceFilterConfiguration}의 RestClientCustomizer 가
 * 현재 MDC 값을 모든 내부 RestClient 호출에 자동으로 실어 보낸다.
 */
public class AuditClient {

    private static final Logger log = LoggerFactory.getLogger(AuditClient.class);

    private final RestClient client;
    private final String internalKey;

    public AuditClient(RestClient.Builder builder,
                       @Value("${auth.service-url}") String authUrl,
                       @Value("${internal.api-key:}") String internalKey) {
        this.client = builder.baseUrl(authUrl).build();
        this.internalKey = internalKey;
    }

    public void record(Long actorId, String action, String targetType, Long targetId,
                       boolean allowed, String reason) {
        try {
            client.post()
                    .uri("/internal/audit")
                    .header(InternalAuth.API_KEY_HEADER, internalKey)
                    .body(Map.of(
                            "actorId", actorId,
                            "action", action,
                            "targetType", targetType,
                            "targetId", targetId,
                            "allowed", allowed,
                            "reason", reason))
                    .retrieve()
                    .toBodilessEntity();
        } catch (Exception e) {
            log.warn("감사 기록 실패. action={} targetId={} 사유={}", action, targetId, e.toString());
        }
    }
}

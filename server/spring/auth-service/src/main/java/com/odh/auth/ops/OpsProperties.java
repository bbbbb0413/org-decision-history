package com.odh.auth.ops;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 운영 현황이 상태를 물어볼 다른 서비스 주소.
 * auth-service 자신은 이 목록에 없다 — 응답하고 있다는 사실 자체가 자기 상태다.
 */
@ConfigurationProperties(prefix = "ops")
public record OpsProperties(String gatewayUrl, String documentUrl, String indexUrl, String chatUrl) {
}

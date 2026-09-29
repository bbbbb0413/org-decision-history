package com.odh.gateway.config;

import org.junit.jupiter.api.Test;
import org.springframework.mock.http.server.reactive.MockServerHttpRequest;
import org.springframework.mock.web.server.MockServerWebExchange;

import java.net.InetSocketAddress;

import static org.assertj.core.api.Assertions.assertThat;

/** 로그인 rate limiting의 키 산정. */
class RateLimitConfigTest {

    private final RateLimitConfig config = new RateLimitConfig();

    @Test
    void resolvesRemoteAddressAsKey() {
        var request = MockServerHttpRequest.get("/api/auth/login")
                .remoteAddress(new InetSocketAddress("203.0.113.5", 54321))
                .build();
        var exchange = MockServerWebExchange.from(request);

        String key = config.clientIpKeyResolver().resolve(exchange).block();

        assertThat(key).isEqualTo("203.0.113.5");
    }

    @Test
    void fallsBackToUnknown_whenRemoteAddressMissing() {
        var request = MockServerHttpRequest.get("/api/auth/login").build();
        var exchange = MockServerWebExchange.from(request);

        String key = config.clientIpKeyResolver().resolve(exchange).block();

        assertThat(key).isEqualTo("unknown");
    }
}

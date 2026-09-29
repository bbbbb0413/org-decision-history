package com.odh.gateway.config;

import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver;
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import reactor.core.publisher.Mono;

/**
 * 공개 엔드포인트(로그인) 요청 속도 제한 — 무차별 대입 공격 방어.
 *
 * 로그인은 인증 전이라 사용자 식별이 안 돼 클라이언트 IP로 키를 잡는다. 신뢰할 수 있는
 * 리버스 프록시(X-Forwarded-For)가 앞단에 없는 지금 구성에서는 원격 주소를 그대로 쓴다 —
 * 프록시를 앞에 두게 되면 그 프록시가 설정한 헤더만 신뢰하도록 바꿔야 한다.
 */
@Configuration
public class RateLimitConfig {

    @Bean
    public RedisRateLimiter loginRateLimiter(GatewayProperties props) {
        return new RedisRateLimiter(props.loginRateLimitPerSecond(), props.loginRateLimitBurst(), 1);
    }

    @Bean
    public KeyResolver clientIpKeyResolver() {
        return exchange -> {
            var remote = exchange.getRequest().getRemoteAddress();
            String ip = (remote != null && remote.getAddress() != null)
                    ? remote.getAddress().getHostAddress()
                    : "unknown";
            return Mono.just(ip);
        };
    }
}

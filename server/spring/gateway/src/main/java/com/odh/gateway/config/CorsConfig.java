package com.odh.gateway.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.reactive.CorsWebFilter;
import org.springframework.web.cors.reactive.UrlBasedCorsConfigurationSource;

import java.util.List;

/**
 * 프론트가 nginx 프록시 없이 게이트웨이를 직접 호출하면서 필요해진 CORS 허용.
 *
 * SSE 요청은 Authorization 헤더를 실어 보내 preflight(OPTIONS) 대상이 된다.
 * {@link CorsWebFilter}는 일반 {@link org.springframework.web.server.WebFilter}로, 라우팅과
 * {@link com.odh.gateway.filter.AuthenticationFilter} 같은 GlobalFilter보다 앞서 실행되므로
 * preflight 요청이 인증 필터를 거치지 않고 여기서 바로 응답된다.
 */
@Configuration
public class CorsConfig {

    @Bean
    public CorsWebFilter corsWebFilter(GatewayProperties properties) {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(List.of(properties.frontOrigin().split(",")));
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("Authorization", "Content-Type"));
        config.setExposedHeaders(List.of("X-Correlation-Id"));
        config.setMaxAge(3600L);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return new CorsWebFilter(source);
    }
}

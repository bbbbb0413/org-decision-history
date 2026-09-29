package com.odh.common.servlet;

import com.odh.common.Correlation;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestClientCustomizer;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;

/** 서블릿 기반 서비스 4개가 공통으로 쓰는 필터 등록. */
@Configuration
public class ServiceFilterConfiguration {

    @Bean
    public FilterRegistrationBean<CorrelationIdFilter> correlationIdFilter() {
        var registration = new FilterRegistrationBean<>(new CorrelationIdFilter());
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return registration;
    }

    @Bean
    public FilterRegistrationBean<InternalKeyFilter> internalKeyFilter(
            @Value("${internal.api-key:}") String apiKey) {
        var registration = new FilterRegistrationBean<>(new InternalKeyFilter(apiKey));
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE + 10);
        return registration;
    }

    /**
     * 서비스 간 내부 호출(RestClient)에 현재 요청의 상관관계 ID 를 그대로 실어 보낸다.
     * 이게 없으면 다른 서비스로 나가는 요청마다 그 서비스의 {@link CorrelationIdFilter}가
     * 새 ID를 발급해버려서, 문서·채팅 서비스가 auth-service 에 남기는 감사 기록이 원래 요청과 다른
     * 상관관계 ID로 끊겨 보인다 — 운영 추적(getOpsTrace)으로 요청 하나를 서비스 경계 너머까지 쫓아갈 수 없게 된다.
     */
    @Bean
    public RestClientCustomizer correlationIdRestClientCustomizer() {
        return builder -> builder.requestInterceptor((request, body, execution) -> {
            String correlationId = MDC.get(Correlation.MDC_KEY);
            if (correlationId != null && !correlationId.isBlank()) {
                request.getHeaders().add(Correlation.HEADER, correlationId);
            }
            return execution.execute(request, body);
        });
    }
}

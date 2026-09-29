package com.odh.gateway.filter;

import com.odh.common.Correlation;
import org.springframework.cloud.gateway.filter.GatewayFilterChain;
import org.springframework.cloud.gateway.filter.GlobalFilter;
import org.springframework.core.Ordered;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import reactor.util.context.Context;

/**
 * 상관관계 ID 를 요청마다 발급해 하위 서비스로 전파하고 응답 헤더로도 돌려준다.
 *
 * MSA 에서 하나의 요청이 게이트웨이부터 워커까지 지나간 자취를 하나의 ID 로 잇는 기준이다.
 * 클라이언트가 값을 보내와도 신뢰하지 않고 게이트웨이가 항상 새로 발급한다.
 */
@Component
public class CorrelationIdWebFilter implements GlobalFilter, Ordered {

    @Override
    public int getOrder() {
        return Ordered.HIGHEST_PRECEDENCE;
    }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, GatewayFilterChain chain) {
        String correlationId = Correlation.newId();

        exchange.getResponse().getHeaders().set(Correlation.HEADER, correlationId);

        ServerWebExchange mutated = exchange.mutate()
                .request(exchange.getRequest().mutate()
                        .header(Correlation.HEADER, correlationId)
                        .build())
                .build();

        return chain.filter(mutated)
                .contextWrite(Context.of(Correlation.MDC_KEY, correlationId));
    }
}

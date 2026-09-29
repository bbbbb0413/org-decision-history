package com.odh.common.servlet;

import com.odh.common.Correlation;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * 게이트웨이가 발급한 상관관계 ID 를 MDC 에 실어 이 서비스의 모든 로그에 붙인다.
 * 게이트웨이를 거치지 않은 요청(로컬 직접 호출)은 여기서 새로 발급한다.
 */
@Order(Ordered.HIGHEST_PRECEDENCE)
public class CorrelationIdFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String correlationId = request.getHeader(Correlation.HEADER);
        if (correlationId == null || correlationId.isBlank()) {
            correlationId = Correlation.newId();
        }
        MDC.put(Correlation.MDC_KEY, correlationId);
        response.setHeader(Correlation.HEADER, correlationId);
        try {
            chain.doFilter(request, response);
        } finally {
            MDC.remove(Correlation.MDC_KEY);
        }
    }
}

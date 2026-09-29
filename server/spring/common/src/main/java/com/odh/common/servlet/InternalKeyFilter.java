package com.odh.common.servlet;

import com.odh.common.Correlation;
import com.odh.common.ErrorCode;
import com.odh.common.InternalAuth;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Set;

/**
 * 서비스 간 호출 검증.
 *
 * 게이트웨이는 모든 하위 호출에 등록된 API 키를 붙인다. 키가 없거나 다르면 거부해
 * 게이트웨이를 우회한 직접 호출을 막는다. 헬스체크 경로는 컨테이너 오케스트레이터가
 * 키 없이 호출하므로 예외로 둔다.
 */
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class InternalKeyFilter extends OncePerRequestFilter {

    /**
     * 내부 키 없이 통과시키는 경로.
     * - /actuator : 컨테이너 오케스트레이터가 키 없이 호출한다
     * - /.well-known : JWKS 는 공개키만 제공하며 게이트웨이가 기동 시 받아간다
     */
    private static final Set<String> OPEN_PREFIXES = Set.of("/actuator", "/.well-known");

    private final String expectedKey;

    public InternalKeyFilter(String expectedKey) {
        this.expectedKey = expectedKey;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return OPEN_PREFIXES.stream().anyMatch(path::startsWith);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String provided = request.getHeader(InternalAuth.API_KEY_HEADER);
        if (expectedKey == null || expectedKey.isBlank() || !expectedKey.equals(provided)) {
            writeUnauthorized(response);
            return;
        }
        chain.doFilter(request, response);
    }

    private void writeUnauthorized(HttpServletResponse response) throws IOException {
        response.setStatus(HttpStatus.UNAUTHORIZED.value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        response.getWriter().write("""
                {"code":"%s","message":"내부 서비스 호출 검증에 실패했습니다.","correlationId":"%s"}
                """.formatted(ErrorCode.INTERNAL_AUTH_FAILED,
                MDC.get(Correlation.MDC_KEY) == null ? "" : MDC.get(Correlation.MDC_KEY)));
    }
}

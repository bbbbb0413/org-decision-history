package com.odh.auth.policy;

import com.odh.common.Correlation;
import org.slf4j.MDC;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * 권한 허용·차단, 정책 변경, 원본 다운로드 발급 이력을 남긴다.
 *
 * 별도 트랜잭션으로 기록한다. 본 작업이 실패해 롤백되어도 시도한 사실은 남아야 한다.
 */
@Service
public class AuditService {

    private static final String INSERT = """
            INSERT INTO audit_log (actor_id, action, target_type, target_id, result, reason, correlation_id)
            VALUES (:actorId, CAST(:action AS audit_action), :targetType, :targetId,
                    CAST(:result AS audit_result), :reason, :correlationId)
            """;

    private final JdbcClient jdbc;

    public AuditService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void record(Long actorId, String action, String targetType, Long targetId,
                       boolean allowed, String reason) {
        jdbc.sql(INSERT)
                .param("actorId", actorId)
                .param("action", action)
                .param("targetType", targetType)
                .param("targetId", targetId)
                .param("result", allowed ? "ALLOWED" : "DENIED")
                .param("reason", reason)
                .param("correlationId", MDC.get(Correlation.MDC_KEY))
                .update();
    }
}

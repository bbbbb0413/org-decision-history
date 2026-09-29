package com.odh.auth.ops;

import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.util.List;

/**
 * 색인 현황과 오류 집계.
 *
 * index-worker 의 indexing 스키마를 조회 전용으로 직접 읽는다. index-worker 가 이미
 * document.document 를 같은 방식으로 조회 전용 조인하고 있어(IndexQueryService), 같은
 * DB 사용자를 공유하는 이 프로젝트에서는 새 내부 엔드포인트를 추가하는 대신 이 방식을 따른다.
 * 쓰기는 하지 않는다. 스키마가 아직 없거나(초기 기동 순서) 접근에 실패하면 빈 결과로 내려간다.
 */
@Service
@Transactional(readOnly = true)
public class OpsIndexService {

    private static final Logger log = LoggerFactory.getLogger(OpsIndexService.class);

    private final JdbcClient jdbc;

    public OpsIndexService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public OpsDtos.OpsIndexStatus indexStatus() {
        try {
            return jdbc.sql("""
                            SELECT
                                count(*) FILTER (WHERE status = 'PENDING') AS pending_count,
                                count(*) FILTER (WHERE status = 'RUNNING') AS running_count,
                                count(*) FILTER (WHERE status = 'FAILED')  AS fail_count,
                                min(requested_at) FILTER (WHERE status = 'PENDING') AS oldest_pending
                            FROM indexing.index_job
                            """)
                    .query((rs, i) -> {
                        var oldestPending = rs.getTimestamp("oldest_pending");
                        long lagSeconds = oldestPending == null ? 0
                                : Duration.between(oldestPending.toInstant(), Instant.now()).getSeconds();
                        return new OpsDtos.OpsIndexStatus(
                                rs.getInt("pending_count"), rs.getInt("running_count"),
                                rs.getInt("fail_count"), Math.max(lagSeconds, 0));
                    }).single();
        } catch (Exception e) {
            log.warn("색인 현황 조회 실패: {}", e.getMessage());
            return new OpsDtos.OpsIndexStatus(0, 0, 0, 0);
        }
    }

    public PageResponse<OpsDtos.OpsError> errors(String service, int page, int size) {
        PageRequest paging = PageRequest.of(page, size);
        String sql = """
                SELECT * FROM (
                    SELECT a.occurred_at, 'auth-service' AS service, a.reason AS message, a.correlation_id
                    FROM audit_log a
                    WHERE a.result = 'DENIED' AND a.reason IS NOT NULL
                    UNION ALL
                    SELECT i.last_attempt_at, 'index-worker', i.failure_reason, CAST(NULL AS varchar)
                    FROM indexing.index_job_item i
                    WHERE i.status = 'FAILED' AND i.failure_reason IS NOT NULL
                ) errors
                WHERE (CAST(:service AS varchar) IS NULL OR service = CAST(:service AS varchar))
                """;

        List<OpsDtos.OpsError> content;
        long total;
        try {
            content = jdbc.sql(sql + " ORDER BY occurred_at DESC LIMIT :limit OFFSET :offset")
                    .param("service", service)
                    .param("limit", paging.size()).param("offset", paging.offset())
                    .query((rs, i) -> new OpsDtos.OpsError(
                            rs.getTimestamp("occurred_at").toInstant(),
                            rs.getString("service"),
                            rs.getString("message"),
                            rs.getString("correlation_id")))
                    .list();
            total = jdbc.sql("SELECT count(*) FROM (" + sql + ") c")
                    .param("service", service).query(Long.class).single();
        } catch (Exception e) {
            log.warn("색인 오류 조회에 색인 스키마를 포함하지 못했다. auth-service 기록만 반환한다: {}", e.getMessage());
            content = authOnlyErrors(service, paging);
            total = content.size();
        }
        return PageResponse.of(content, paging.page(), paging.size(), total);
    }

    private List<OpsDtos.OpsError> authOnlyErrors(String service, PageRequest paging) {
        if (service != null && !service.equals("auth-service")) {
            return List.of();
        }
        return jdbc.sql("""
                        SELECT occurred_at, reason, correlation_id FROM audit_log
                        WHERE result = 'DENIED' AND reason IS NOT NULL
                        ORDER BY occurred_at DESC
                        LIMIT :limit OFFSET :offset
                        """)
                .param("limit", paging.size()).param("offset", paging.offset())
                .query((rs, i) -> new OpsDtos.OpsError(
                        rs.getTimestamp("occurred_at").toInstant(), "auth-service",
                        rs.getString("reason"), rs.getString("correlation_id")))
                .list();
    }

    /**
     * 상관관계 ID 로 요청을 추적한다. 이 프로젝트에는 별도 분산 트레이싱 백엔드가 없어,
     * 게이트웨이가 발급한 상관관계 ID로 auth-service 의 감사 로그에 남은 기록을
     * 구간으로 보여준다. document-service·chat-service 도 자기 서비스에서 일어난 감사
     * 대상 행위(문서 열람, 검색 등)를 이 감사 로그에 함께 남기고 내부 호출에 상관관계 ID를
     * 그대로 실어 보내므로(RestClientCustomizer, AnswerJobProducer/Worker 의 Kafka 헤더 전파),
     * 감사 대상으로 등록된 행위에 한해서는 여러 서비스에 걸친 구간이 한 트레이스로 모인다.
     * 감사 로그에 남지 않는 내부 처리 구간(예: 검색 알고리즘 내부 단계)까지는 잡히지 않는다.
     * 지속 시간은 기록하지 않으므로 0 으로 둔다 — 실제 소요 시간을 만들어내지 않는다.
     */
    public List<OpsDtos.TraceSpan> trace(String correlationId) {
        return jdbc.sql("""
                        SELECT occurred_at, action, result, reason FROM audit_log
                        WHERE correlation_id = :correlationId
                        ORDER BY occurred_at
                        """)
                .param("correlationId", correlationId)
                .query((rs, i) -> {
                    String action = rs.getString("action");
                    return new OpsDtos.TraceSpan(
                            serviceOf(action), action,
                            rs.getTimestamp("occurred_at").toInstant(), 0,
                            "DENIED".equals(rs.getString("result")) ? "ERROR" : "OK",
                            rs.getString("reason"));
                })
                .list();
    }

    /**
     * audit_log 는 어느 서비스가 기록했는지는 저장하지 않는다 — 각 서비스가 남기는
     * 액션 종류가 고정돼 있어(document-service/chat-service 의 AuditClient 호출부 참고)
     * action 값으로 origin 서비스를 되짚는다.
     */
    private static String serviceOf(String action) {
        return switch (action) {
            case "DOCUMENT_VIEW", "FILE_DOWNLOAD_ISSUE" -> "document-service";
            case "DOCUMENT_SEARCH" -> "chat-service";
            default -> "auth-service"; // ROLE_CHANGE, POLICY_CHANGE, ORGANIZATION_CREATED, USER_CREATED
        };
    }
}

package com.odh.auth.metrics;

import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * 제품 지표 집계.
 *
 * <p>운영 현황({@code ops} 패키지)과 일부러 나눠 뒀다. 운영 현황은 "제품이 잘 도는가"를,
 * 여기는 "제품이 쓸모 있는가"를 본다. 보는 사람도 판단도 다르다.
 *
 * <p>{@code chat}·{@code document}·{@code indexing} 스키마를 조회 전용으로 직접 읽는다.
 * {@link com.odh.auth.ops.OpsIndexService} 가 같은 방식으로 {@code indexing} 을
 * 읽고 있고 같은 DB 사용자를 공유하므로, 서비스마다 집계 엔드포인트를 새로 내는 대신
 * 그 선례를 따른다. 쓰기는 하지 않는다.
 *
 * <p>구획별로 예외를 잡는다. 스키마 하나를 읽지 못해도 나머지 지표는 보여주고, 대신
 * 실패한 구획 이름을 {@code degraded} 에 담아 올린다 — 조회 실패를 "0건"으로 보여주면
 * 지표를 거꾸로 읽게 된다.
 *
 * <p><b>그래서 {@link #metrics(int)} 와 {@link #feedbackDetail(long)} 는 트랜잭션으로 묶지
 * 않는다.</b> 한 트랜잭션 안에서 SQL 하나가 실패하면 Postgres 가 그 트랜잭션을 abort 시켜
 * 뒤따르는 문장이 모두 {@code current transaction is aborted} 로 실패한다 — try/catch 로
 * 잡아도 되돌릴 수 없다. 클래스에 {@code @Transactional} 을 걸었을 때 실제로
 * {@code indexing} 스키마 하나가 없으면 구획 하나가 아니라 <b>대시보드 전체가</b> 0으로
 * 떨어졌다(테스트 {@code degradesPerSection} 이 잡았다). 구획들은 서로 독립된 읽기이고
 * 대시보드에 한 스냅샷 일관성이 필요하지도 않으므로, 각 문장이 자체 커밋하게 둔다.
 *
 * <h2>삭제된 대화는 세지 않는다</h2>
 *
 * <p>대화 삭제는 소프트 삭제다({@code chat.conversation.deleted_at}). 지표 SQL 이
 * 기간 조건만 걸면, 사용자가 대화를 지워도 그 답변이 지표 1·2·3·4·8 의 분모에 영구히
 * 남는다 — 화면에서 사라진 대화가 숫자에는 계속 살아 있게 된다.
 *
 * <p>그래서 메시지를 세는 모든 자리에 {@code chat.conversation} 조인을 걸어
 * {@code deleted_at IS NULL} 인 것만 센다. 피드백 목록·드릴다운도 같은 기준으로 맞췄다 —
 * 다르게 두면 목록에는 있는데 지표에는 없는 건이 생겨 어느 쪽이 맞는지 알 수 없게 된다.
 *
 * <p>"전달된 답변을 없던 일로 만드는 게 맞나"는 논쟁거리이지만, 사용자가 지운 대화가
 * 관리자 지표에 계속 남는 쪽이 더 설명하기 어렵다고 판단했다. 감사 로그(지표 7)는 이
 * 규칙과 무관하다 — 그건 대화가 아니라 접근 기록이다.
 */
@Service
public class ProductMetricsService {

    private static final Logger log = LoggerFactory.getLogger(ProductMetricsService.class);

    /** 핵심 지표 4의 일반 질문 목표(5초). 타임라인 질문 목표(10초)는 여기서 측정하지 않는다. */
    private static final long LATENCY_TARGET_MS = 5_000;

    /** 조회 기간 상한. 1년을 넘는 집계는 대시보드 용도를 벗어나고 스캔 비용만 커진다. */
    private static final int MAX_DAYS = 365;
    private static final int DEFAULT_DAYS = 30;

    /** 목록에서 걸 수 있는 평가값. 임의 문자열이 SQL 비교로 흘러가지 않게 허용 목록으로 막는다. */
    private static final Set<String> ALLOWED_RATINGS = Set.of("HELPFUL", "NOT_HELPFUL");

    private final JdbcClient jdbc;

    public ProductMetricsService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 음수·0·과대 입력을 기본값과 상한으로 맞춘다. {@code PageRequest} 와 같은 보정 방침이다. */
    public static int clampDays(int days) {
        if (days <= 0) {
            return DEFAULT_DAYS;
        }
        return Math.min(days, MAX_DAYS);
    }

    /**
     * 기간 경계를 SQL 파라미터로 바꾼다.
     *
     * <p>{@code Instant} 를 그대로 바인딩하면 안 된다 — pgjdbc 의 {@code PgPreparedStatement} 는
     * {@code LocalDateTime}·{@code OffsetDateTime} 만 다루고 {@code Instant} 는 타입을 추론하지
     * 못해 예외를 던진다. 그러면 아래 {@code section()} 이 그걸 잡아 <b>모든 지표가 0으로
     * 보이는데 오류는 안 보이는</b> 상태가 된다(실제로 한 번 그렇게 됐다).
     */
    private static OffsetDateTime boundary(Instant instant) {
        return instant.atOffset(ZoneOffset.UTC);
    }

    public MetricsDtos.ProductMetrics metrics(int days) {
        int safeDays = clampDays(days);
        Instant to = Instant.now();
        Instant from = to.minus(Duration.ofDays(safeDays));
        List<String> degraded = new ArrayList<>();

        var answerQuality = section(degraded, "answerQuality",
                () -> answerQuality(from), MetricsDtos.AnswerQuality.empty());
        var sources = section(degraded, "sources",
                () -> sourceVerifiability(from), MetricsDtos.SourceVerifiability.empty());
        var feedback = section(degraded, "feedback",
                () -> feedback(from), MetricsDtos.Feedback.empty());
        var latency = section(degraded, "latency",
                () -> latency(from), MetricsDtos.Latency.empty(LATENCY_TARGET_MS));
        var denials = section(degraded, "denials",
                () -> denials(from), MetricsDtos.AccessDenials.empty());
        var adoption = section(degraded, "adoption",
                () -> adoption(from), MetricsDtos.Adoption.empty());

        return new MetricsDtos.ProductMetrics(from, to, safeDays,
                answerQuality, sources, feedback, latency, denials, adoption, List.copyOf(degraded));
    }

    // ---- 지표 1: 출처 포함 답변 성공률 ----

    private MetricsDtos.AnswerQuality answerQuality(Instant from) {
        var totals = jdbc.sql("""
                        SELECT count(*)                                                       AS assistant_messages,
                               count(*) FILTER (WHERE src.cnt > 0)                            AS with_sources,
                               count(*) FILTER (WHERE m.answer_status = 'OK')                 AS ok,
                               count(*) FILTER (WHERE m.answer_status = 'OK' AND src.cnt > 0) AS ok_with_sources
                        FROM chat.message m
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        LEFT JOIN LATERAL (
                            SELECT count(*) AS cnt FROM chat.answer_source s WHERE s.message_id = m.id
                        ) src ON TRUE
                        WHERE m.role = 'ASSISTANT' AND m.created_at >= :from
                        """)
                .param("from", boundary(from))
                .query((rs, i) -> new long[]{
                        rs.getLong("assistant_messages"), rs.getLong("with_sources"),
                        rs.getLong("ok"), rs.getLong("ok_with_sources")})
                .single();

        List<MetricsDtos.StatusCount> byStatus = jdbc.sql("""
                        SELECT COALESCE(m.answer_status::text, 'UNKNOWN') AS status, count(*) AS cnt
                        FROM chat.message m
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        WHERE m.role = 'ASSISTANT' AND m.created_at >= :from
                        GROUP BY 1
                        ORDER BY cnt DESC
                        """)
                .param("from", boundary(from))
                .query((rs, i) -> new MetricsDtos.StatusCount(rs.getString("status"), rs.getLong("cnt")))
                .list();

        return new MetricsDtos.AnswerQuality(totals[0], totals[1], totals[2], totals[3], byStatus);
    }

    // ---- 지표 2: 출처의 사용자 검증 가능 비율 ----

    private MetricsDtos.SourceVerifiability sourceVerifiability(Instant from) {
        // chat → document → indexing 세 스키마를 걸친다. 문서나 청크가 지워졌으면 LEFT JOIN 이
        // NULL 로 남고 "검증 불가"로 세어진다 — 사용자가 겪는 상태와 같으므로 그게 맞다.
        //
        // 파일은 "인용된 판 이하에서 가장 최근에 올라온 것"으로 찾는다. 정확한 판으로만
        // 조인하면 본문 오타를 한 번 고친 문서가 그 뒤로 전부 "검증 불가"가 됐다 — 사용자는
        // 여전히 원본을 받을 수 있는데(화면도 같은 규칙을 쓴다) 지표만 떨어졌다.
        return jdbc.sql("""
                        SELECT count(*)                                        AS cited,
                               count(*) FILTER (WHERE df.id IS NOT NULL)        AS with_file,
                               count(*) FILTER (WHERE c.bboxes IS NOT NULL)     AS with_coords
                        FROM chat.answer_source s
                        JOIN chat.message m ON m.id = s.message_id
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        LEFT JOIN LATERAL (
                            SELECT f.id
                            FROM document.document_file f
                            WHERE f.document_id = s.document_id
                              AND f.version <= s.document_version
                            ORDER BY f.version DESC
                            LIMIT 1
                        ) df ON TRUE
                        LEFT JOIN indexing.document_chunk c
                               ON c.document_id = s.document_id
                              AND c.document_version = s.document_version
                              AND c.chunk_no = s.chunk_no
                        WHERE m.created_at >= :from
                        """)
                .param("from", boundary(from))
                .query((rs, i) -> new MetricsDtos.SourceVerifiability(
                        rs.getLong("cited"), rs.getLong("with_file"), rs.getLong("with_coords")))
                .single();
    }

    // ---- 지표 3: 유용하다고 평가된 답변 비율 ----

    private MetricsDtos.Feedback feedback(Instant from) {
        return jdbc.sql("""
                        SELECT
                            (SELECT count(*) FROM chat.answer_feedback f
                               JOIN chat.message m ON m.id = f.message_id
                               JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                              WHERE f.created_at >= :from)                                AS responded,
                            (SELECT count(*) FROM chat.answer_feedback f
                               JOIN chat.message m ON m.id = f.message_id
                               JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                              WHERE f.created_at >= :from AND f.rating = 'HELPFUL')       AS helpful,
                            (SELECT count(*) FROM chat.answer_feedback f
                               JOIN chat.message m ON m.id = f.message_id
                               JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                              WHERE f.created_at >= :from AND f.rating = 'NOT_HELPFUL')   AS not_helpful,
                            (SELECT count(*) FROM chat.message m
                               JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                              WHERE m.role = 'ASSISTANT' AND m.created_at >= :from)       AS assistant_messages
                        """)
                .param("from", boundary(from))
                .query((rs, i) -> new MetricsDtos.Feedback(
                        rs.getLong("responded"), rs.getLong("helpful"),
                        rs.getLong("not_helpful"), rs.getLong("assistant_messages")))
                .single();
    }

    // ---- 지표 4: 평균 응답 시간 ----

    private MetricsDtos.Latency latency(Instant from) {
        // percentile_disc 에 FILTER 를 걸어 NULL latency 가 분위값으로 뽑히지 않게 한다.
        return jdbc.sql("""
                        SELECT count(*) FILTER (WHERE m.latency_ms IS NOT NULL)        AS measured,
                               avg(m.latency_ms)                                        AS avg_ms,
                               percentile_disc(0.95) WITHIN GROUP (ORDER BY m.latency_ms)
                                   FILTER (WHERE m.latency_ms IS NOT NULL)              AS p95_ms,
                               count(*) FILTER (WHERE m.latency_ms > :targetMs)         AS over_target
                        FROM chat.message m
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        WHERE m.role = 'ASSISTANT' AND m.created_at >= :from
                        """)
                .param("from", boundary(from))
                .param("targetMs", LATENCY_TARGET_MS)
                .query((rs, i) -> {
                    long measured = rs.getLong("measured");
                    var avg = rs.getBigDecimal("avg_ms");
                    long p95 = rs.getLong("p95_ms");
                    boolean p95Null = rs.wasNull();
                    return new MetricsDtos.Latency(
                            measured,
                            avg == null ? null : avg.longValue(),
                            p95Null ? null : p95,
                            rs.getLong("over_target"),
                            LATENCY_TARGET_MS);
                })
                .single();
    }

    // ---- 지표 7 관련: 권한 차단 ----

    private MetricsDtos.AccessDenials denials(Instant from) {
        long total = jdbc.sql("""
                        SELECT count(*) FROM audit_log
                        WHERE result = 'DENIED' AND occurred_at >= :from
                        """)
                .param("from", boundary(from))
                .query(Long.class).single();

        List<MetricsDtos.ActionCount> byAction = jdbc.sql("""
                        SELECT action::text AS action, count(*) AS cnt FROM audit_log
                        WHERE result = 'DENIED' AND occurred_at >= :from
                        GROUP BY 1
                        ORDER BY cnt DESC
                        """)
                .param("from", boundary(from))
                .query((rs, i) -> new MetricsDtos.ActionCount(rs.getString("action"), rs.getLong("cnt")))
                .list();

        return new MetricsDtos.AccessDenials(total, byAction);
    }

    // ---- 지표 8: 활성 사용자 중 질문한 비율 ----

    private MetricsDtos.Adoption adoption(Instant from) {
        return jdbc.sql("""
                        SELECT
                            (SELECT count(*) FROM app_user WHERE status = 'ACTIVE')      AS active_users,
                            (SELECT count(DISTINCT c.owner_id)
                               FROM chat.message m
                               JOIN chat.conversation c ON c.id = m.conversation_id
                              WHERE m.role = 'USER' AND m.created_at >= :from
                                AND c.deleted_at IS NULL)                                AS asked_users,
                            (SELECT count(*) FROM chat.message m
                               JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                              WHERE m.role = 'USER' AND m.created_at >= :from)           AS questions
                        """)
                .param("from", boundary(from))
                .query((rs, i) -> new MetricsDtos.Adoption(
                        rs.getLong("active_users"), rs.getLong("asked_users"), rs.getLong("questions")))
                .single();
    }

    // ---- 피드백 드릴다운 ----

    /**
     * 평가가 달린 답변 목록. {@code rating} 이 허용 목록에 없으면 필터 없이 전체를 돌려준다
     * — 잘못된 필터값에 500 을 내거나 빈 목록으로 오해를 주지 않는다.
     */
    @Transactional(readOnly = true)
    public PageResponse<MetricsDtos.FeedbackItem> feedbackItems(String rating, int page, int size) {
        PageRequest paging = PageRequest.of(page, size);
        String safeRating = (rating != null && ALLOWED_RATINGS.contains(rating.trim().toUpperCase()))
                ? rating.trim().toUpperCase()
                : null;

        long total = jdbc.sql("""
                        SELECT count(*) FROM chat.answer_feedback f
                        JOIN chat.message m ON m.id = f.message_id
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        WHERE (CAST(:rating AS varchar) IS NULL OR f.rating::text = CAST(:rating AS varchar))
                        """)
                .param("rating", safeRating)
                .query(Long.class).single();

        List<MetricsDtos.FeedbackItem> content = jdbc.sql("""
                        SELECT f.message_id, m.conversation_id, f.rating::text AS rating, f.reason,
                               f.created_at, u.name AS rater_name,
                               q.content AS question,
                               left(m.content, 300) AS answer_preview,
                               m.answer_status::text AS answer_status, m.latency_ms,
                               (SELECT count(*) FROM chat.answer_source s WHERE s.message_id = m.id) AS source_count
                        FROM chat.answer_feedback f
                        JOIN chat.message m ON m.id = f.message_id
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        LEFT JOIN app_user u ON u.id = f.user_id
                        LEFT JOIN LATERAL (
                            SELECT qm.content FROM chat.message qm
                            WHERE qm.conversation_id = m.conversation_id
                              AND qm.role = 'USER'
                              AND (qm.created_at, qm.id) <= (m.created_at, m.id)
                            ORDER BY qm.created_at DESC, qm.id DESC
                            LIMIT 1
                        ) q ON TRUE
                        WHERE (CAST(:rating AS varchar) IS NULL OR f.rating::text = CAST(:rating AS varchar))
                        ORDER BY f.created_at DESC, f.message_id DESC
                        LIMIT :limit OFFSET :offset
                        """)
                .param("rating", safeRating)
                .param("limit", paging.size())
                .param("offset", paging.offset())
                .query((rs, i) -> new MetricsDtos.FeedbackItem(
                        rs.getLong("message_id"), rs.getLong("conversation_id"),
                        rs.getString("rating"), rs.getString("reason"),
                        rs.getTimestamp("created_at").toInstant(), rs.getString("rater_name"),
                        rs.getString("question"), rs.getString("answer_preview"),
                        rs.getString("answer_status"), (Integer) rs.getObject("latency_ms"),
                        rs.getLong("source_count")))
                .list();

        return PageResponse.of(content, paging.page(), paging.size(), total);
    }

    /**
     * 평가가 달린 답변 하나의 전문과 인용 출처. 없으면 비어 있다.
     *
     * <p>트랜잭션으로 묶지 않는다 — {@link #citedSources(long)} 가 {@code document}·
     * {@code indexing} 을 조인해 실패할 수 있고, 같은 트랜잭션이면 그 실패가 본문 조회까지
     * 끌고 내려간다(클래스 주석 참고). 출처를 못 읽어도 질문·답변 전문은 보여줘야 한다.
     */
    public Optional<MetricsDtos.FeedbackDetail> feedbackDetail(long messageId) {
        // 출처는 별도 쿼리라 먼저 가져와 DTO 를 한 번에 만든다.
        List<MetricsDtos.CitedSource> sources = citedSources(messageId);
        return jdbc.sql("""
                        SELECT f.message_id, m.conversation_id, f.rating::text AS rating, f.reason,
                               f.created_at, u.name AS rater_name,
                               q.content AS question, m.rewritten_query, m.content AS answer,
                               m.answer_status::text AS answer_status, m.model_name, m.latency_ms,
                               m.created_at AS answered_at
                        FROM chat.answer_feedback f
                        JOIN chat.message m ON m.id = f.message_id
                        JOIN chat.conversation cv ON cv.id = m.conversation_id AND cv.deleted_at IS NULL
                        LEFT JOIN app_user u ON u.id = f.user_id
                        LEFT JOIN LATERAL (
                            SELECT qm.content FROM chat.message qm
                            WHERE qm.conversation_id = m.conversation_id
                              AND qm.role = 'USER'
                              AND (qm.created_at, qm.id) <= (m.created_at, m.id)
                            ORDER BY qm.created_at DESC, qm.id DESC
                            LIMIT 1
                        ) q ON TRUE
                        WHERE f.message_id = :messageId
                        """)
                .param("messageId", messageId)
                .query((rs, i) -> new MetricsDtos.FeedbackDetail(
                        rs.getLong("message_id"), rs.getLong("conversation_id"),
                        rs.getString("rating"), rs.getString("reason"),
                        rs.getTimestamp("created_at").toInstant(), rs.getString("rater_name"),
                        rs.getString("question"), rs.getString("rewritten_query"),
                        rs.getString("answer"), rs.getString("answer_status"),
                        rs.getString("model_name"), (Integer) rs.getObject("latency_ms"),
                        rs.getTimestamp("answered_at").toInstant(),
                        sources))
                .optional();
    }

    private List<MetricsDtos.CitedSource> citedSources(long messageId) {
        try {
            return jdbc.sql("""
                            SELECT s.document_id, s.document_version, s.chunk_no, s.rank, s.similarity,
                                   d.title AS document_title,
                                   c.section_path,
                                   (df.id IS NOT NULL) AS has_file
                            FROM chat.answer_source s
                            LEFT JOIN document.document d ON d.id = s.document_id
                            -- 지표 2와 같은 규칙으로 찾는다(인용된 판 이하에서 가장 최근 파일).
                            -- 다르게 두면 드릴다운의 "원본 있음"과 지표 숫자가 어긋난다.
                            LEFT JOIN LATERAL (
                                SELECT f.id
                                FROM document.document_file f
                                WHERE f.document_id = s.document_id
                                  AND f.version <= s.document_version
                                ORDER BY f.version DESC
                                LIMIT 1
                            ) df ON TRUE
                            LEFT JOIN indexing.document_chunk c
                                   ON c.document_id = s.document_id
                                  AND c.document_version = s.document_version
                                  AND c.chunk_no = s.chunk_no
                            WHERE s.message_id = :messageId
                            ORDER BY s.rank
                            """)
                    .param("messageId", messageId)
                    .query((rs, i) -> {
                        var similarity = rs.getBigDecimal("similarity");
                        return new MetricsDtos.CitedSource(
                                rs.getLong("document_id"),
                                // 문서가 지워졌으면 제목이 없다. 화면에서 그대로 보이게 표시를 남긴다.
                                rs.getString("document_title") == null
                                        ? "(삭제된 문서 #" + rs.getLong("document_id") + ")"
                                        : rs.getString("document_title"),
                                rs.getInt("document_version"), rs.getInt("chunk_no"), rs.getInt("rank"),
                                similarity == null ? null : similarity.doubleValue(),
                                rs.getString("section_path"),
                                rs.getBoolean("has_file"));
                    })
                    .list();
        } catch (Exception e) {
            // 출처를 못 읽어도 질문·답변 전문은 보여준다. 그것만으로도 원인 대부분이 보인다.
            log.warn("인용 출처를 읽지 못했다(messageId={}): {}", messageId, e.getMessage());
            return List.of();
        }
    }

    /** 구획 하나를 읽고, 실패하면 이름을 {@code degraded} 에 남기고 빈 값으로 대체한다. */
    private <T> T section(List<String> degraded, String name, java.util.function.Supplier<T> load, T fallback) {
        try {
            return load.get();
        } catch (Exception e) {
            log.warn("제품 지표 구획을 읽지 못했다({}): {}", name, e.getMessage());
            degraded.add(name);
            return fallback;
        }
    }
}

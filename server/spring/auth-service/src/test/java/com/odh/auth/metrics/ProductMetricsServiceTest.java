package com.odh.auth.metrics;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 제품 지표 집계.
 *
 * <p>실제 Postgres 로 돌린다. 이 집계는 {@code FILTER}, {@code LATERAL},
 * {@code percentile_disc ... WITHIN GROUP ... FILTER}, 행 비교({@code (a,b) <= (c,d)}),
 * 네이티브 enum 캐스팅에 기대고 있어 H2 로는 같은 방언을 재현할 수 없다 —
 * {@code PermissionScopeServiceTest} 와 같은 이유다.
 *
 * <p>{@code chat}·{@code document}·{@code indexing} 스키마는 다른 서비스가 소유하므로 이
 * 컨테이너에는 없다. governance-worker 테스트가 하는 것과 같은 방식으로 필요한 만큼만
 * 스텁을 세운다. 실제 마이그레이션과 같은 컬럼·타입을 쓴다.
 */
@SpringBootTest
@Testcontainers
class ProductMetricsServiceTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private ProductMetricsService service;

    @Autowired
    private JdbcClient jdbc;

    private long helpfulMessageId;
    private long notHelpfulMessageId;

    @BeforeEach
    void seed() {
        jdbc.sql("DROP SCHEMA IF EXISTS chat CASCADE").update();
        jdbc.sql("DROP SCHEMA IF EXISTS document CASCADE").update();
        jdbc.sql("DROP SCHEMA IF EXISTS indexing CASCADE").update();
        jdbc.sql("TRUNCATE audit_log, access_policy, user_role, user_team, app_user, team, organization "
                + "RESTART IDENTITY CASCADE").update();

        createChatSchema();
        createDocumentSchema();
        createIndexingSchema();

        long org = insertOrg("플랫폼본부");
        long u1 = insertUser("a@t.com", "김하늘", org, "ACTIVE");
        long u2 = insertUser("b@t.com", "이도윤", org, "ACTIVE");
        insertUser("c@t.com", "박서준", org, "ACTIVE");
        insertUser("d@t.com", "최유진", org, "DISABLED");

        long c1 = insertConversation(u1);
        long c2 = insertConversation(u2);

        // c1: 질문 2건, 답변 2건 — 하나는 출처 있고 빠르며, 하나는 출처 없고 느리다
        insertMessage(c1, "USER", "재택근무 정책이 뭐야?", null, null, 10);
        long a1 = insertMessage(c1, "ASSISTANT", "주 2회까지 가능하다 [1]", "OK", 1_200, 9);
        insertMessage(c1, "USER", "출장비는?", null, null, 8);
        long a2 = insertMessage(c1, "ASSISTANT", "확인이 필요하다", "OK", 7_000, 7);

        // c2: 근거를 못 찾은 답변과, 낮은 신뢰도로 끝나 latency 가 남지 않은 답변
        insertMessage(c2, "USER", "결제 정책 알려줘", null, null, 6);
        long a3 = insertMessage(c2, "ASSISTANT", "근거를 찾지 못했다", "NO_EVIDENCE", 800, 5);
        long a4 = insertMessage(c2, "ASSISTANT", "확신이 낮다 [1]", "LOW_CONFIDENCE", null, 4);

        // 문서 1은 원본 파일이 있고, 문서 2는 없다
        long doc1 = insertDocument("재택근무 지침");
        long doc2 = insertDocument("출장비 규정");
        insertDocumentFile(doc1, 1);

        // 청크: (doc1,1,0) 만 좌표가 있다 — PDF 만 좌표를 가진다는 실제 조건을 그대로 둔다
        insertChunk(doc1, 1, 0, "1장 > 신청", "[{\"page\":1,\"x\":0.1,\"y\":0.2,\"w\":0.3,\"h\":0.04}]");
        insertChunk(doc1, 1, 1, "1장 > 한도", null);
        insertChunk(doc2, 1, 0, null, null);

        insertSource(a1, doc1, 1, 0, 1, "0.9210");
        insertSource(a1, doc2, 1, 0, 2, null);
        insertSource(a4, doc1, 1, 1, 1, "0.7100");

        insertFeedback(a1, u1, "HELPFUL", null);
        insertFeedback(a3, u2, "NOT_HELPFUL", "근거가 질문과 무관했다");
        helpfulMessageId = a1;
        notHelpfulMessageId = a3;

        insertAudit(u2, "DOCUMENT_VIEW", "DENIED", "권한 범위 밖 문서");
        insertAudit(u2, "POLICY_VIEW", "DENIED", "정책 조회 권한 없음");
        insertAudit(u1, "DOCUMENT_VIEW", "ALLOWED", null);

        // a2 는 출처가 없다는 사실 자체가 지표 1의 분자에서 빠지는 근거다
        assertThat(a2).isPositive();
    }

    @Test
    @DisplayName("지표 1 — 출처를 인용한 OK 답변만 성공으로 센다")
    void answerQuality() {
        var quality = service.metrics(30).answerQuality();

        assertThat(quality.assistantMessages()).isEqualTo(4);
        assertThat(quality.withSources()).isEqualTo(2);      // a1, a4
        assertThat(quality.ok()).isEqualTo(2);               // a1, a2
        assertThat(quality.okWithSources()).isEqualTo(1);    // a1 만
        assertThat(quality.byStatus())
                .extracting(MetricsDtos.StatusCount::status, MetricsDtos.StatusCount::count)
                .containsExactlyInAnyOrder(
                        org.assertj.core.groups.Tuple.tuple("OK", 2L),
                        org.assertj.core.groups.Tuple.tuple("NO_EVIDENCE", 1L),
                        org.assertj.core.groups.Tuple.tuple("LOW_CONFIDENCE", 1L));
    }

    @Test
    @DisplayName("지표 2 — 원본 파일 보유를 검증 가능 기준으로 세고, 좌표는 따로 센다")
    void sourceVerifiability() {
        var sources = service.metrics(30).sources();

        assertThat(sources.citedSources()).isEqualTo(3);
        assertThat(sources.withOriginalFile()).isEqualTo(2);  // doc1 을 가리키는 둘
        assertThat(sources.withCoordinates()).isEqualTo(1);   // (doc1,1,0) 만
    }

    @Test
    @DisplayName("지표 3 — 유용 비율과 응답률을 함께 낸다")
    void feedback() {
        var feedback = service.metrics(30).feedback();

        assertThat(feedback.responded()).isEqualTo(2);
        assertThat(feedback.helpful()).isEqualTo(1);
        assertThat(feedback.notHelpful()).isEqualTo(1);
        // 응답률의 분모. 답변 4건 중 2건만 평가됐다는 사실이 비율 해석에 필요하다
        assertThat(feedback.assistantMessages()).isEqualTo(4);
    }

    @Test
    @DisplayName("지표 4 — latency 가 없는 답변은 평균·분위에서 빠진다")
    void latency() {
        var latency = service.metrics(30).latency();

        assertThat(latency.measured()).isEqualTo(3);          // a4 는 latency 가 없다
        assertThat(latency.averageMs()).isEqualTo(3_000);     // (1200+7000+800)/3
        assertThat(latency.p95Ms()).isEqualTo(7_000);
        assertThat(latency.overTargetCount()).isEqualTo(1);   // 7000ms 한 건
        assertThat(latency.targetMs()).isEqualTo(5_000);
    }

    @Test
    @DisplayName("지표 7 — 차단만 세고 허용은 세지 않는다")
    void denials() {
        var denials = service.metrics(30).denials();

        assertThat(denials.total()).isEqualTo(2);
        assertThat(denials.byAction())
                .extracting(MetricsDtos.ActionCount::action)
                .containsExactlyInAnyOrder("DOCUMENT_VIEW", "POLICY_VIEW");
    }

    @Test
    @DisplayName("지표 8 — 분모는 활성 계정이고 비활성 계정은 빠진다")
    void adoption() {
        var adoption = service.metrics(30).adoption();

        assertThat(adoption.activeUsers()).isEqualTo(3);      // DISABLED 한 명 제외
        assertThat(adoption.askedUsers()).isEqualTo(2);       // 질문한 소유자 두 명
        assertThat(adoption.questions()).isEqualTo(3);
    }

    @Test
    @DisplayName("기간 밖 데이터는 집계에서 빠진다")
    void excludesDataOutsidePeriod() {
        var sevenDays = service.metrics(7);
        assertThat(sevenDays.answerQuality().assistantMessages()).isEqualTo(4);

        // 40일 전으로 밀면 7일 집계에서 사라진다
        jdbc.sql("UPDATE chat.message SET created_at = now() - interval '40 days'").update();
        assertThat(service.metrics(7).answerQuality().assistantMessages()).isZero();
        assertThat(service.metrics(90).answerQuality().assistantMessages()).isEqualTo(4);
    }

    @Test
    @DisplayName("days 는 기본 30, 1~365 로 보정된다")
    void clampsDays() {
        assertThat(ProductMetricsService.clampDays(0)).isEqualTo(30);
        assertThat(ProductMetricsService.clampDays(-5)).isEqualTo(30);
        assertThat(ProductMetricsService.clampDays(9_999)).isEqualTo(365);
        assertThat(ProductMetricsService.clampDays(7)).isEqualTo(7);
        assertThat(service.metrics(-1).days()).isEqualTo(30);
    }

    @Test
    @DisplayName("스키마 하나를 못 읽어도 나머지 지표는 내려가고, 실패를 0으로 감추지 않는다")
    void degradesPerSection() {
        jdbc.sql("DROP SCHEMA indexing CASCADE").update();

        var metrics = service.metrics(30);

        // 지표 2 는 indexing 을 조인하므로 실패한다
        assertThat(metrics.degraded()).contains("sources");
        assertThat(metrics.sources().citedSources()).isZero();
        // 나머지는 그대로 나온다
        assertThat(metrics.answerQuality().assistantMessages()).isEqualTo(4);
        assertThat(metrics.adoption().activeUsers()).isEqualTo(3);
        assertThat(metrics.degraded()).doesNotContain("answerQuality", "adoption");
    }

    @Test
    @DisplayName("정상일 때 degraded 는 비어 있다 — 조용히 0으로 떨어진 걸 통과로 읽지 않기 위해")
    void nothingDegradedWhenHealthy() {
        assertThat(service.metrics(30).degraded()).isEmpty();
    }

    @Test
    @DisplayName("출처를 못 읽어도 질문·답변 전문은 그대로 나온다")
    void feedbackDetailSurvivesMissingSourceSchema() {
        jdbc.sql("DROP SCHEMA indexing CASCADE").update();

        var detail = service.feedbackDetail(helpfulMessageId).orElseThrow();

        assertThat(detail.question()).isEqualTo("재택근무 정책이 뭐야?");
        assertThat(detail.answer()).isEqualTo("주 2회까지 가능하다 [1]");
        assertThat(detail.sources()).isEmpty();
    }

    @Test
    @DisplayName("피드백 목록 — 평가에 달린 질문과 답변 미리보기를 함께 준다")
    void feedbackItems() {
        var page = service.feedbackItems("NOT_HELPFUL", 0, 20);

        assertThat(page.totalElements()).isEqualTo(1);
        var item = page.content().getFirst();
        assertThat(item.messageId()).isEqualTo(notHelpfulMessageId);
        assertThat(item.rating()).isEqualTo("NOT_HELPFUL");
        assertThat(item.reason()).isEqualTo("근거가 질문과 무관했다");
        assertThat(item.question()).isEqualTo("결제 정책 알려줘");
        assertThat(item.answerPreview()).contains("근거를 찾지 못했다");
        assertThat(item.answerStatus()).isEqualTo("NO_EVIDENCE");
        assertThat(item.sourceCount()).isZero();
    }

    @Test
    @DisplayName("피드백 목록 — 알 수 없는 rating 은 필터 없이 전체를 준다")
    void unknownRatingFallsBackToAll() {
        assertThat(service.feedbackItems("MAYBE", 0, 20).totalElements()).isEqualTo(2);
        assertThat(service.feedbackItems(null, 0, 20).totalElements()).isEqualTo(2);
        assertThat(service.feedbackItems("helpful", 0, 20).totalElements()).isEqualTo(1);
    }

    @Test
    @DisplayName("피드백 상세 — 답변 전문과 인용 출처를 순위대로 준다")
    void feedbackDetail() {
        var detail = service.feedbackDetail(helpfulMessageId).orElseThrow();

        assertThat(detail.question()).isEqualTo("재택근무 정책이 뭐야?");
        assertThat(detail.answer()).isEqualTo("주 2회까지 가능하다 [1]");
        assertThat(detail.rating()).isEqualTo("HELPFUL");
        assertThat(detail.raterName()).isEqualTo("김하늘");
        assertThat(detail.sources()).hasSize(2);

        var first = detail.sources().getFirst();
        assertThat(first.rank()).isEqualTo(1);
        assertThat(first.documentTitle()).isEqualTo("재택근무 지침");
        assertThat(first.similarity()).isEqualTo(0.9210);
        assertThat(first.sectionPath()).isEqualTo("1장 > 신청");
        assertThat(first.hasOriginalFile()).isTrue();

        var second = detail.sources().get(1);
        assertThat(second.documentTitle()).isEqualTo("출장비 규정");
        // 전문검색으로만 걸린 근거는 유사도가 없다
        assertThat(second.similarity()).isNull();
        assertThat(second.hasOriginalFile()).isFalse();
    }

    @Test
    @DisplayName("피드백 상세 — 평가가 없는 메시지는 비어 있다")
    void feedbackDetailMissing() {
        assertThat(service.feedbackDetail(999_999L)).isEmpty();
    }

    @Test
    @DisplayName("삭제된 문서를 가리키는 출처도 목록에서 사라지지 않는다")
    void deletedDocumentStillListed() {
        jdbc.sql("DELETE FROM document.document_file WHERE document_id = :id").param("id", 1L).update();
        jdbc.sql("DELETE FROM document.document WHERE id = :id").param("id", 1L).update();

        var detail = service.feedbackDetail(helpfulMessageId).orElseThrow();

        assertThat(detail.sources()).hasSize(2);
        assertThat(detail.sources().getFirst().documentTitle()).contains("삭제된 문서");
        assertThat(detail.sources().getFirst().hasOriginalFile()).isFalse();
    }

    // ---- 스키마 스텁 ----

    private void createChatSchema() {
        jdbc.sql("CREATE SCHEMA chat").update();
        jdbc.sql("CREATE TYPE chat.message_role AS ENUM ('USER', 'ASSISTANT')").update();
        jdbc.sql("CREATE TYPE chat.answer_status AS ENUM "
                + "('OK', 'NO_EVIDENCE', 'LOW_CONFIDENCE', 'DEGRADED', 'RATE_LIMITED')").update();
        jdbc.sql("CREATE TYPE chat.feedback_rating AS ENUM ('HELPFUL', 'NOT_HELPFUL')").update();
        jdbc.sql("""
                CREATE TABLE chat.conversation (
                    id         BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    owner_id   BIGINT       NOT NULL,
                    title      VARCHAR(200) NOT NULL,
                    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
                    deleted_at TIMESTAMPTZ
                )
                """).update();
        jdbc.sql("""
                CREATE TABLE chat.message (
                    id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    conversation_id BIGINT            NOT NULL REFERENCES chat.conversation (id) ON DELETE CASCADE,
                    role            chat.message_role NOT NULL,
                    content         TEXT              NOT NULL,
                    rewritten_query TEXT,
                    model_name      VARCHAR(100),
                    answer_status   chat.answer_status,
                    latency_ms      INT,
                    created_at      TIMESTAMPTZ       NOT NULL DEFAULT now()
                )
                """).update();
        jdbc.sql("""
                CREATE TABLE chat.answer_source (
                    id               BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    message_id       BIGINT       NOT NULL REFERENCES chat.message (id) ON DELETE CASCADE,
                    document_id      BIGINT       NOT NULL,
                    document_version INT          NOT NULL,
                    chunk_no         INT          NOT NULL,
                    similarity       NUMERIC(5,4),
                    rank             SMALLINT     NOT NULL
                )
                """).update();
        jdbc.sql("""
                CREATE TABLE chat.answer_feedback (
                    id         BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    message_id BIGINT               NOT NULL UNIQUE REFERENCES chat.message (id) ON DELETE CASCADE,
                    user_id    BIGINT               NOT NULL,
                    rating     chat.feedback_rating NOT NULL,
                    reason     VARCHAR(500),
                    created_at TIMESTAMPTZ          NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ          NOT NULL DEFAULT now()
                )
                """).update();
    }

    private void createDocumentSchema() {
        jdbc.sql("CREATE SCHEMA document").update();
        jdbc.sql("""
                CREATE TABLE document.document (
                    id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    title           VARCHAR(200) NOT NULL,
                    current_version INT          NOT NULL DEFAULT 1,
                    deleted_at      TIMESTAMPTZ
                )
                """).update();
        jdbc.sql("""
                CREATE TABLE document.document_file (
                    id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    document_id BIGINT NOT NULL REFERENCES document.document (id) ON DELETE CASCADE,
                    version     INT    NOT NULL,
                    CONSTRAINT ux_docfile_ver UNIQUE (document_id, version)
                )
                """).update();
    }

    private void createIndexingSchema() {
        jdbc.sql("CREATE SCHEMA indexing").update();
        jdbc.sql("""
                CREATE TABLE indexing.document_chunk (
                    id               BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    document_id      BIGINT       NOT NULL,
                    document_version INT          NOT NULL,
                    chunk_no         INT          NOT NULL,
                    section_path     VARCHAR(500),
                    page_start       SMALLINT,
                    page_end         SMALLINT,
                    bboxes           JSONB,
                    CONSTRAINT ux_chunk UNIQUE (document_id, document_version, chunk_no)
                )
                """).update();
    }

    // ---- 데이터 삽입 ----

    private long insertOrg(String name) {
        return jdbc.sql("INSERT INTO organization (name) VALUES (:n) RETURNING id")
                .param("n", name).query(Long.class).single();
    }

    private long insertUser(String email, String name, long orgId, String status) {
        return jdbc.sql("""
                        INSERT INTO app_user (email, password_hash, name, organization_id, status)
                        VALUES (:e, 'x', :n, :o, CAST(:s AS user_status)) RETURNING id
                        """)
                .param("e", email).param("n", name).param("o", orgId).param("s", status)
                .query(Long.class).single();
    }

    private long insertConversation(long ownerId) {
        return jdbc.sql("INSERT INTO chat.conversation (owner_id, title) VALUES (:o, '대화') RETURNING id")
                .param("o", ownerId).query(Long.class).single();
    }

    /** {@code minutesAgo} 로 순서를 고정한다 — 질문↔답변 짝짓기가 시각 순서에 기댄다. */
    private long insertMessage(long conversationId, String role, String content,
                               String answerStatus, Integer latencyMs, int minutesAgo) {
        return jdbc.sql("""
                        INSERT INTO chat.message
                            (conversation_id, role, content, answer_status, latency_ms, created_at)
                        VALUES (:c, CAST(:r AS chat.message_role), :content,
                                CAST(:status AS chat.answer_status), :latency,
                                now() - make_interval(mins => :mins))
                        RETURNING id
                        """)
                .param("c", conversationId).param("r", role).param("content", content)
                .param("status", answerStatus).param("latency", latencyMs).param("mins", minutesAgo)
                .query(Long.class).single();
    }

    private long insertDocument(String title) {
        return jdbc.sql("INSERT INTO document.document (title) VALUES (:t) RETURNING id")
                .param("t", title).query(Long.class).single();
    }

    private void insertDocumentFile(long documentId, int version) {
        jdbc.sql("INSERT INTO document.document_file (document_id, version) VALUES (:d, :v)")
                .param("d", documentId).param("v", version).update();
    }

    private void insertChunk(long documentId, int version, int chunkNo, String sectionPath, String bboxes) {
        jdbc.sql("""
                        INSERT INTO indexing.document_chunk
                            (document_id, document_version, chunk_no, section_path, bboxes)
                        VALUES (:d, :v, :c, :s, CAST(:b AS jsonb))
                        """)
                .param("d", documentId).param("v", version).param("c", chunkNo)
                .param("s", sectionPath).param("b", bboxes).update();
    }

    private void insertSource(long messageId, long documentId, int version, int chunkNo,
                              int rank, String similarity) {
        jdbc.sql("""
                        INSERT INTO chat.answer_source
                            (message_id, document_id, document_version, chunk_no, rank, similarity)
                        VALUES (:m, :d, :v, :c, :r, CAST(:sim AS numeric))
                        """)
                .param("m", messageId).param("d", documentId).param("v", version)
                .param("c", chunkNo).param("r", rank).param("sim", similarity).update();
    }

    private void insertFeedback(long messageId, long userId, String rating, String reason) {
        jdbc.sql("""
                        INSERT INTO chat.answer_feedback (message_id, user_id, rating, reason)
                        VALUES (:m, :u, CAST(:r AS chat.feedback_rating), :reason)
                        """)
                .param("m", messageId).param("u", userId).param("r", rating).param("reason", reason)
                .update();
    }

    // ---- 삭제된 대화 제외 ----

    @Test
    @DisplayName("대화를 삭제하면 그 답변이 모든 지표의 분모에서 빠진다")
    void softDeletedConversationLeavesEveryMetric() {
        long owner = insertUser("e@t.com", "한지우", insertOrg("보안본부"), "ACTIVE");
        long doomed = insertConversation(owner);
        insertMessage(doomed, "USER", "곧 지울 질문", null, null, 3);
        long answer = insertMessage(doomed, "ASSISTANT", "곧 지울 답변 [1]", "OK", 2_000, 2);
        insertSource(answer, insertDocument("지울 문서"), 1, 0, 1, "0.5000");
        insertFeedback(answer, owner, "HELPFUL", null);

        var before = service.metrics(30);
        assertThat(before.answerQuality().assistantMessages()).isEqualTo(5);
        assertThat(before.sources().citedSources()).isEqualTo(4);
        assertThat(before.feedback().responded()).isEqualTo(3);
        assertThat(before.latency().measured()).isEqualTo(4);
        assertThat(before.adoption().questions()).isEqualTo(4);
        assertThat(before.adoption().askedUsers()).isEqualTo(3);

        softDelete(doomed);

        // 소프트 삭제라 행은 남아 있다. 그래도 지표에서는 빠져야 한다 —
        // 화면에서 사라진 대화가 관리자 숫자에는 계속 살아 있으면 설명할 수 없다.
        var after = service.metrics(30);
        assertThat(after.answerQuality().assistantMessages()).isEqualTo(4);
        assertThat(after.answerQuality().byStatus())
                .extracting(MetricsDtos.StatusCount::count)
                .doesNotContain(3L);
        assertThat(after.sources().citedSources()).isEqualTo(3);
        assertThat(after.feedback().responded()).isEqualTo(2);
        assertThat(after.feedback().helpful()).isEqualTo(1);
        assertThat(after.feedback().assistantMessages()).isEqualTo(4);
        assertThat(after.latency().measured()).isEqualTo(3);
        assertThat(after.adoption().questions()).isEqualTo(3);
        assertThat(after.adoption().askedUsers()).isEqualTo(2);
        assertThat(after.degraded()).isEmpty();
    }

    @Test
    @DisplayName("삭제된 대화의 평가는 목록과 드릴다운에서도 빠진다")
    void softDeletedConversationLeavesFeedbackDrilldown() {
        long owner = insertUser("f@t.com", "서지호", insertOrg("연구본부"), "ACTIVE");
        long doomed = insertConversation(owner);
        insertMessage(doomed, "USER", "곧 지울 질문", null, null, 3);
        long answer = insertMessage(doomed, "ASSISTANT", "곧 지울 답변", "OK", 1_000, 2);
        insertFeedback(answer, owner, "NOT_HELPFUL", "곧 지울 평가");

        assertThat(service.feedbackItems(null, 0, 20).totalElements()).isEqualTo(3);
        assertThat(service.feedbackDetail(answer)).isPresent();

        softDelete(doomed);

        // 목록과 지표를 다른 기준으로 두면 "목록에는 있는데 지표에는 없는" 건이 생겨
        // 어느 쪽이 맞는지 알 수 없게 된다.
        var items = service.feedbackItems(null, 0, 20);
        assertThat(items.totalElements()).isEqualTo(2);
        assertThat(items.content()).extracting(MetricsDtos.FeedbackItem::messageId).doesNotContain(answer);
        assertThat(service.feedbackDetail(answer)).isEmpty();
    }

    private void softDelete(long conversationId) {
        jdbc.sql("UPDATE chat.conversation SET deleted_at = now() WHERE id = :id")
                .param("id", conversationId).update();
    }

    private void insertAudit(long actorId, String action, String result, String reason) {
        jdbc.sql("""
                        INSERT INTO audit_log (actor_id, action, target_type, result, reason)
                        VALUES (:a, CAST(:action AS audit_action), 'DOCUMENT',
                                CAST(:result AS audit_result), :reason)
                        """)
                .param("a", actorId).param("action", action).param("result", result).param("reason", reason)
                .update();
    }
}

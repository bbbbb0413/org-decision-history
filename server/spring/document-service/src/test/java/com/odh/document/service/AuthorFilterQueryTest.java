package com.odh.document.service;

import com.odh.common.PermissionScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import software.amazon.awssdk.services.s3.S3Client;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * "내 문서" 필터.
 *
 * <p>실제 Postgres 로 돌린다. 상충 쪽 필터가 {@code (sd.author_id = ? OR td.author_id = ?)} 로
 * 문서 테이블을 두 번 조인한 위에 얹히는데, 이게 권한 범위 조건(별칭별로 따로 만들어지는)과
 * 함께 제대로 동작하는지가 요점이다 — {@code ScopePredicateTest} 가 경고하는 그 지점이다.
 */
@SpringBootTest
@Testcontainers
class AuthorFilterQueryTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    /**
     * 조회 SQL 은 테이블을 스키마 없이 쓴다({@code FROM document}). 운영은 JDBC URL 의
     * {@code currentSchema=document,public} 로 search_path 를 맞추므로(application.yml),
     * 컨테이너 URL 에도 같은 것을 붙여야 한다 — 빼면 전부 "relation does not exist" 다.
     */
    private static String jdbcUrlWithSchema() {
        String url = postgres.getJdbcUrl();
        return url + (url.contains("?") ? "&" : "?") + "currentSchema=document,public";
    }

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", AuthorFilterQueryTest::jdbcUrlWithSchema);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
        registry.add("storage.access-key", () -> "author-filter");
        registry.add("storage.secret-key", () -> "author-filter");
    }

    /** OpenApiSnapshotTest 와 같은 이유 — 빈 생성이 MinIO 에 버킷을 물어본다. */
    @MockitoBean
    private S3Client s3Client;

    @Autowired
    private DocumentQueryService documents;

    @Autowired
    private ConflictQueryService conflicts;

    @Autowired
    private JdbcClient jdbc;

    /** 시스템 관리자 범위 — 권한 필터를 통과시켜 작성자 필터만 놓고 본다. */
    private static final PermissionScope ALL =
            new PermissionScope(Set.of(), Set.of(), Set.of(), Set.of(), true);

    private static final long ME = 10L;
    private static final long SOMEONE_ELSE = 20L;

    private long myDoc;
    private long theirDoc;
    private long orphanDoc;

    @BeforeEach
    void seed() {
        jdbc.sql("TRUNCATE document.decision_conflicts, document.document RESTART IDENTITY CASCADE").update();

        myDoc = insertDocument("내가 쓴 재택근무 지침", ME, "김하늘");
        theirDoc = insertDocument("남이 쓴 출장비 규정", SOMEONE_ELSE, "이도윤");
        orphanDoc = insertDocument("가져온 문서", null, "알 수 없음");
    }

    @Test
    @DisplayName("문서 목록 — authorId 를 주면 내가 쓴 것만 나온다")
    void documentsFilteredByAuthor() {
        var mine = documents.search(ALL, null, null, null, null, null, ME, 0, 20);

        assertThat(mine.content()).extracting("id").containsExactly(myDoc);
        assertThat(mine.totalElements()).isEqualTo(1);
    }

    @Test
    @DisplayName("문서 목록 — authorId 가 null 이면 거르지 않는다")
    void documentsUnfilteredWhenAuthorIdNull() {
        var all = documents.search(ALL, null, null, null, null, null, null, 0, 20);

        assertThat(all.content()).extracting("id")
                .containsExactlyInAnyOrder(myDoc, theirDoc, orphanDoc);
    }

    @Test
    @DisplayName("상충 목록 — 내 문서가 source 든 target 이든 걸리면 보인다")
    void conflictsMatchEitherSide() {
        long iAmSource = insertConflict(myDoc, theirDoc, "내가 source");
        long iAmTarget = insertConflict(theirDoc, myDoc, "내가 target");
        insertConflict(theirDoc, orphanDoc, "나와 무관");

        var mine = conflicts.listConflicts(null, ME, 0, 20, ALL);

        assertThat(mine.content()).extracting("id")
                .containsExactlyInAnyOrder(iAmSource, iAmTarget);
        assertThat(mine.totalElements()).isEqualTo(2);
    }

    @Test
    @DisplayName("상충 목록 — 작성자 필터와 상태 필터가 함께 걸린다")
    void conflictsCombineAuthorAndStatus() {
        long open = insertConflict(myDoc, theirDoc, "열린 건");
        long resolved = insertConflict(theirDoc, myDoc, "닫힌 건");
        jdbc.sql("UPDATE document.decision_conflicts SET status = 'RESOLVED' WHERE id = :id")
                .param("id", resolved).update();

        var openMine = conflicts.listConflicts("OPEN", ME, 0, 20, ALL);

        assertThat(openMine.content()).extracting("id").containsExactly(open);
    }

    @Test
    @DisplayName("상충 목록 — authorId 가 null 이면 전부 나온다")
    void conflictsUnfilteredWhenAuthorIdNull() {
        insertConflict(myDoc, theirDoc, "A");
        insertConflict(theirDoc, orphanDoc, "B");

        assertThat(conflicts.listConflicts(null, null, 0, 20, ALL).totalElements()).isEqualTo(2);
    }

    @Test
    @DisplayName("작성자가 없는 문서만 걸린 상충은 내 목록에 없다")
    void orphanAuthorNeverMatches() {
        insertConflict(theirDoc, orphanDoc, "작성자 없음");

        assertThat(conflicts.listConflicts(null, ME, 0, 20, ALL).content()).isEmpty();
    }

    private long insertDocument(String title, Long authorId, String authorName) {
        return jdbc.sql("""
                        INSERT INTO document.document
                            (title, content, document_type, decision_status, organization_id,
                             author_id, author_name, authored_at)
                        VALUES (:title, '본문', 'POLICY_DOC', 'CURRENT', 1,
                                :authorId, :authorName, DATE '2026-01-10')
                        RETURNING id
                        """)
                .param("title", title).param("authorId", authorId).param("authorName", authorName)
                .query(Long.class).single();
    }

    private long insertConflict(long sourceId, long targetId, String clause) {
        return jdbc.sql("""
                        INSERT INTO document.decision_conflicts
                            (source_doc_id, target_doc_id, source_clause, target_clause,
                             conflict_type, severity_score, reasoning, affected_teams, status)
                        VALUES (:source, :target, :clause, '상대 조항',
                                'DIRECT_CONTRADICTION', 0.90, '사유', '{}', 'OPEN')
                        RETURNING id
                        """)
                .param("source", sourceId).param("target", targetId).param("clause", clause)
                .query(Long.class).single();
    }
}

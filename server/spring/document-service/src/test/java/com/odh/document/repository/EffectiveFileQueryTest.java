package com.odh.document.repository;

import com.odh.document.domain.DocumentFile;
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

import static org.assertj.core.api.Assertions.assertThat;

/**
 * "지금 이 문서의 원본" 조회 규칙.
 *
 * <p>본문만 고쳐도 판이 올라가는데(`Document.update`) 파일은 올린 판에 묶여 있다. 정확한 판으로만
 * 찾으면 오타 하나만 고쳐도 원본이 사라지고, 색인은 본문 기반으로 돌아가 PDF 좌표를 잃고,
 * 지표 2(출처 검증 가능 비율)까지 떨어진다. 그래서 **그 판 이하에서 가장 최근에 올라온 파일**을 쓴다.
 *
 * <p>같은 규칙이 세 군데에 있다 — 문서 상세(JPA), 색인기(`documents.py` 의 LATERAL 조인),
 * 지표 2·드릴다운(`ProductMetricsService` 의 LATERAL 조인). 여기서는 JPA 파생 쿼리가
 * 의도대로 도는지 실제 Postgres 로 확인한다.
 */
@SpringBootTest
@Testcontainers
class EffectiveFileQueryTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    /** 조회 SQL 이 스키마 없이 테이블을 쓰므로 search_path 를 맞춘다(AuthorFilterQueryTest 와 같은 이유). */
    private static String jdbcUrlWithSchema() {
        String url = postgres.getJdbcUrl();
        return url + (url.contains("?") ? "&" : "?") + "currentSchema=document,public";
    }

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", EffectiveFileQueryTest::jdbcUrlWithSchema);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
        registry.add("storage.access-key", () -> "effective-file");
        registry.add("storage.secret-key", () -> "effective-file");
    }

    @MockitoBean
    private S3Client s3Client;

    @Autowired
    private DocumentFileRepository files;

    @Autowired
    private JdbcClient jdbc;

    private long documentId;

    @BeforeEach
    void seed() {
        jdbc.sql("TRUNCATE document.document_file, document.document RESTART IDENTITY CASCADE").update();
        documentId = jdbc.sql("""
                        INSERT INTO document.document
                            (title, content, document_type, decision_status, organization_id,
                             author_id, author_name, authored_at, current_version)
                        VALUES ('보안 로그 보관 기간', '## 결정 사항', CAST('DECISION_RECORD' AS document_type),
                                CAST('CURRENT' AS decision_status), 1, 10, '이도윤', CURRENT_DATE, 1)
                        RETURNING id
                        """)
                .query(Long.class).single();
    }

    private void addFile(int version, String name) {
        files.save(DocumentFile.of(documentId, version, "documents/%d/v%d/%s".formatted(documentId, version, name),
                name, "application/pdf", 1024L, "c".repeat(64), 10L));
        files.flush();
    }

    @Test
    @DisplayName("파일이 올라온 판에서는 그 파일을 쓴다")
    void usesFileOfSameVersion() {
        addFile(1, "원본.pdf");

        assertThat(files.findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(documentId, 1))
                .isPresent()
                .get().extracting(DocumentFile::getOriginalFilename).isEqualTo("원본.pdf");
    }

    @Test
    @DisplayName("본문만 고쳐 판이 올라가도 이전 판의 원본을 이어 쓴다")
    void inheritsFileFromEarlierVersion() {
        addFile(1, "원본.pdf");

        // 판 3까지 올라갔지만 파일은 판 1에만 있다 — 오타를 두 번 고친 상태
        var found = files.findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(documentId, 3);

        assertThat(found).isPresent();
        assertThat(found.get().getVersion()).isEqualTo(1);
        assertThat(found.get().getOriginalFilename()).isEqualTo("원본.pdf");
    }

    @Test
    @DisplayName("원본이 바뀌어 다시 올리면 새 파일이 우선한다 — 따로 이어붙이지 않아도 된다")
    void newUploadWins() {
        addFile(1, "원본.pdf");
        addFile(3, "원본_수정.pdf");

        var found = files.findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(documentId, 3);

        assertThat(found).isPresent();
        assertThat(found.get().getVersion()).isEqualTo(3);
        assertThat(found.get().getOriginalFilename()).isEqualTo("원본_수정.pdf");
    }

    @Test
    @DisplayName("과거 판을 볼 때는 그 시점 이후에 올라온 파일을 쓰지 않는다")
    void doesNotLookForward() {
        addFile(3, "원본_수정.pdf");

        // 판 2 기준으로 물으면 판 3의 파일은 아직 없던 것이다
        assertThat(files.findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(documentId, 2))
                .isEmpty();
    }

    @Test
    @DisplayName("파일이 한 번도 올라오지 않았으면 없다")
    void emptyWhenNoFileEverUploaded() {
        assertThat(files.findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(documentId, 5))
                .isEmpty();
    }

    @Test
    @DisplayName("버전 이력용 조회는 여전히 그 판에 올라온 것만 본다")
    void exactVersionLookupIsUnchanged() {
        addFile(1, "원본.pdf");

        // 버전 이력 화면의 질문은 "이 판에 무엇이 올라왔나"다. 상속시키면 올리지 않은 판에도
        // 파일이 있었던 것처럼 보인다.
        assertThat(files.findByDocumentIdAndVersion(documentId, 1)).isPresent();
        assertThat(files.findByDocumentIdAndVersion(documentId, 2)).isEmpty();
    }
}

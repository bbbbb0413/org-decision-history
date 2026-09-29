package com.odh.document.importing;

import com.odh.document.domain.DocumentType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * 가져오기 작업 조회.
 *
 * 성공한 항목의 추출값(extractedTitle 등)은 import_item 에 따로 저장하지 않고
 * 등록된 document 를 그대로 조인해 보여준다 — 가져온 문서의 제목·작성자·작성일이
 * 곧 문서 자체의 값이므로 두 곳에 같은 값을 중복해 둘 이유가 없다.
 */
@Service
@Transactional(readOnly = true)
public class ImportQueryService {

    private static final String JOB_COLUMNS = """
            j.job_key, j.status::text AS status, j.file_count, j.success_count, j.fail_count,
            j.default_document_type::text AS default_document_type,
            j.organization_id, j.team_id, j.requested_at, j.finished_at
            """;

    private final JdbcClient jdbc;

    public ImportQueryService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<ImportDtos.Job> getJob(UUID jobKey) {
        return jdbc.sql("SELECT %s FROM import_job j WHERE j.job_key = CAST(:key AS uuid)".formatted(JOB_COLUMNS))
                .param("key", jobKey.toString())
                .query(this::mapJob)
                .optional();
    }

    public Optional<Long> getJobId(UUID jobKey) {
        return jdbc.sql("SELECT id FROM import_job WHERE job_key = CAST(:key AS uuid)")
                .param("key", jobKey.toString()).query(Long.class).optional();
    }

    public List<ImportDtos.Item> listItems(UUID jobKey) {
        return jdbc.sql("""
                        SELECT i.id, i.filename, i.document_id, i.status::text AS status,
                               i.failure_reason, i.retryable,
                               d.title AS extracted_title, d.author_name AS extracted_author_name,
                               d.authored_at AS extracted_authored_at
                        FROM import_item i
                        JOIN import_job j ON j.id = i.import_job_id
                        LEFT JOIN document d ON d.id = i.document_id
                        WHERE j.job_key = CAST(:key AS uuid)
                        ORDER BY i.id
                        """)
                .param("key", jobKey.toString())
                .query((rs, n) -> new ImportDtos.Item(
                        rs.getLong("id"),
                        rs.getString("filename"),
                        (Long) rs.getObject("document_id"),
                        rs.getString("extracted_title"),
                        rs.getString("extracted_author_name"),
                        rs.getDate("extracted_authored_at") == null ? null : rs.getDate("extracted_authored_at").toLocalDate(),
                        rs.getString("status"),
                        rs.getString("failure_reason"),
                        rs.getBoolean("retryable")))
                .list();
    }

    private ImportDtos.Job mapJob(java.sql.ResultSet rs, int rowNum) throws java.sql.SQLException {
        return new ImportDtos.Job(
                UUID.fromString(rs.getString("job_key")),
                rs.getString("status"),
                rs.getInt("file_count"), rs.getInt("success_count"), rs.getInt("fail_count"),
                DocumentType.valueOf(rs.getString("default_document_type")),
                rs.getLong("organization_id"), (Long) rs.getObject("team_id"),
                rs.getTimestamp("requested_at").toInstant(),
                rs.getTimestamp("finished_at") == null ? null : rs.getTimestamp("finished_at").toInstant());
    }
}

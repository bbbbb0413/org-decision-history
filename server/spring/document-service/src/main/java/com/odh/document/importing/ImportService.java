package com.odh.document.importing;

import com.odh.document.domain.DocumentType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/** 일괄 가져오기 접수와 재시도. */
@Service
public class ImportService {

    private final JdbcClient jdbc;
    private final ImportQueryService query;
    private final ImportProcessor processor;

    public ImportService(JdbcClient jdbc, ImportQueryService query, ImportProcessor processor) {
        this.jdbc = jdbc;
        this.query = query;
        this.processor = processor;
    }

    @Transactional
    public ImportDtos.Job create(List<MultipartFile> uploads, DocumentType defaultType,
                                 Long organizationId, Long teamId, Long userId) throws IOException {
        if (uploads == null || uploads.isEmpty()) {
            throw new ValidationException("files", "가져올 파일을 한 개 이상 선택해 주세요.");
        }

        List<ImportProcessor.PendingFile> pending = new ArrayList<>(uploads.size());
        for (MultipartFile upload : uploads) {
            pending.add(new ImportProcessor.PendingFile(upload.getOriginalFilename(), upload.getBytes()));
        }

        UUID jobKey = UUID.randomUUID();
        Long jobId = jdbc.sql("""
                        INSERT INTO import_job
                            (job_key, requested_by, default_document_type, organization_id, team_id, file_count)
                        VALUES (CAST(:jobKey AS uuid), :requestedBy, CAST(:defaultType AS document_type),
                                :organizationId, :teamId, :fileCount)
                        RETURNING id
                        """)
                .param("jobKey", jobKey.toString())
                .param("requestedBy", userId)
                .param("defaultType", defaultType.name())
                .param("organizationId", organizationId)
                .param("teamId", teamId)
                .param("fileCount", pending.size())
                .query(Long.class).single();

        processor.run(jobKey, jobId, pending, defaultType, organizationId, teamId, userId);

        return query.getJob(jobKey).orElseThrow();
    }

    public ImportDtos.JobDetail getDetail(UUID jobKey) {
        ImportDtos.Job job = query.getJob(jobKey).orElseThrow(ImportJobNotFoundException::new);
        return ImportDtos.JobDetail.of(job, query.listItems(jobKey));
    }

    /**
     * 재시도 API 는 요청 본문이 없다. 조직·팀·작성자는 원래 접수 당시 값을 그대로 쓴다 —
     * 같은 가져오기 배치의 후속 처리이므로 최초 접수자 기준을 유지한다.
     */
    public ImportDtos.Job retry(UUID jobKey) {
        JobOwner owner = jdbc.sql("""
                        SELECT id, requested_by, default_document_type::text AS default_document_type,
                               organization_id, team_id
                        FROM import_job WHERE job_key = CAST(:key AS uuid)
                        """)
                .param("key", jobKey.toString())
                .query((rs, i) -> new JobOwner(
                        rs.getLong("id"), rs.getLong("requested_by"),
                        DocumentType.valueOf(rs.getString("default_document_type")),
                        rs.getLong("organization_id"), (Long) rs.getObject("team_id")))
                .optional().orElseThrow(ImportJobNotFoundException::new);

        boolean hasRetryable = jdbc.sql("""
                        SELECT count(*) FROM import_item
                        WHERE import_job_id = :id AND status = 'FAILED' AND retryable = TRUE
                        """)
                .param("id", owner.jobId()).query(Long.class).single() > 0;
        if (!hasRetryable) {
            throw new NoRetryableItemsException();
        }

        processor.runRetry(jobKey, owner.jobId(), owner.defaultType(), owner.organizationId(), owner.teamId(), owner.requestedBy());
        return query.getJob(jobKey).orElseThrow();
    }

    private record JobOwner(Long jobId, Long requestedBy, DocumentType defaultType, Long organizationId, Long teamId) {
    }

    public static class ValidationException extends RuntimeException {
        private final String field;
        public ValidationException(String field, String message) {
            super(message);
            this.field = field;
        }
        public String field() {
            return field;
        }
    }

    public static class ImportJobNotFoundException extends RuntimeException {
    }

    public static class NoRetryableItemsException extends RuntimeException {
    }
}

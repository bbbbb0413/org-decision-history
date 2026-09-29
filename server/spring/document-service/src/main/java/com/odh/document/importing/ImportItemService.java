package com.odh.document.importing;

import com.odh.document.domain.Document;
import com.odh.document.domain.DocumentFile;
import com.odh.document.domain.DocumentType;
import com.odh.document.domain.DocumentVersion;
import com.odh.document.domain.OutboxEventType;
import com.odh.document.repository.DocumentFileRepository;
import com.odh.document.repository.DocumentRepository;
import com.odh.document.repository.DocumentVersionRepository;
import com.odh.document.service.OutboxPublisher;
import com.odh.document.storage.FileStorageService;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.util.List;

/**
 * 가져오기 파일 한 건의 처리와 결과 기록.
 *
 * ImportProcessor(@Async) 가 자기 자신을 호출하면 프록시를 거치지 않아 @Transactional 이
 * 조용히 빠진다. 그래서 실제 DB 작업은 별도 빈인 이 클래스에 둔다.
 */
@Service
public class ImportItemService {

    private final JdbcClient jdbc;
    private final DocumentRepository documents;
    private final DocumentVersionRepository versions;
    private final DocumentFileRepository files;
    private final FileStorageService storage;
    private final OutboxPublisher outbox;
    private final DocumentMetadataExtractor extractor;

    public ImportItemService(JdbcClient jdbc, DocumentRepository documents, DocumentVersionRepository versions,
                             DocumentFileRepository files, FileStorageService storage,
                             OutboxPublisher outbox, DocumentMetadataExtractor extractor) {
        this.jdbc = jdbc;
        this.documents = documents;
        this.versions = versions;
        this.files = files;
        this.storage = storage;
        this.outbox = outbox;
        this.extractor = extractor;
    }

    public void markRunning(Long jobId) {
        jdbc.sql("UPDATE import_job SET status = CAST('RUNNING' AS job_status) WHERE id = :id")
                .param("id", jobId).update();
    }

    public void markFinished(Long jobId) {
        jdbc.sql("UPDATE import_job SET status = CAST('COMPLETED' AS job_status), finished_at = now() WHERE id = :id")
                .param("id", jobId).update();
    }

    /**
     * 파일 한 건을 문서로 등록한다. 추출·저장·색인 접수를 한 트랜잭션에 묶어,
     * 저장에 실패하면(형식·크기) 방금 만든 문서도 함께 롤백한다 — 원본 없는 문서가 남지 않는다.
     */
    @Transactional
    public Long attemptImport(byte[] bytes, String filename, DocumentType defaultType,
                              Long organizationId, Long teamId, Long userId) {
        DocumentMetadataExtractor.Extracted extracted = extractor.extract(bytes, filename);

        String title = extracted.title() != null ? extracted.title() : titleFromFilename(filename);
        String author = extracted.author() != null ? extracted.author() : "사용자 " + userId;
        LocalDate authoredAt = extracted.authoredAt() != null ? extracted.authoredAt() : LocalDate.now();

        Document document = documents.save(Document.createImported(
                title, extracted.content(), defaultType, organizationId, teamId, userId, author, authoredAt));

        var stored = storage.store(document.getId(), 1, bytes, filename);
        files.save(DocumentFile.of(document.getId(), 1, stored.storageKey(), filename,
                stored.mimeType(), stored.sizeBytes(), stored.checksum(), userId));
        versions.save(DocumentVersion.snapshot(document, "일괄 가져오기", userId));
        outbox.record(document, OutboxEventType.DOCUMENT_CREATED);

        return document.getId();
    }

    @Transactional
    public void recordSuccess(Long jobId, String filename, Long documentId) {
        insertItem(jobId, filename, documentId, "SUCCESS", null, true, null);
        jdbc.sql("UPDATE import_job SET success_count = success_count + 1 WHERE id = :id")
                .param("id", jobId).update();
    }

    /** @param stagingKey 재시도 가능한 실패만 채운다. 재시도 불가면 null 이다 */
    @Transactional
    public void recordFailure(Long jobId, String filename, String reason, boolean retryable, String stagingKey) {
        insertItem(jobId, filename, null, "FAILED", truncate(reason), retryable, stagingKey);
        jdbc.sql("UPDATE import_job SET fail_count = fail_count + 1 WHERE id = :id")
                .param("id", jobId).update();
    }

    private void insertItem(Long jobId, String filename, Long documentId, String status,
                            String reason, boolean retryable, String stagingKey) {
        jdbc.sql("""
                        INSERT INTO import_item
                            (import_job_id, filename, document_id, status, failure_reason, retryable, staging_key)
                        VALUES (:jobId, :filename, :documentId, CAST(:status AS import_item_status),
                                :reason, :retryable, :stagingKey)
                        """)
                .param("jobId", jobId).param("filename", filename).param("documentId", documentId)
                .param("status", status).param("reason", reason).param("retryable", retryable)
                .param("stagingKey", stagingKey)
                .update();
    }

    public record RetryCandidate(Long itemId, String filename, String stagingKey) {
    }

    public List<RetryCandidate> retryCandidates(Long jobId) {
        return jdbc.sql("""
                        SELECT id, filename, staging_key FROM import_item
                        WHERE import_job_id = :jobId AND status = 'FAILED' AND retryable = TRUE
                        """)
                .param("jobId", jobId)
                .query((rs, i) -> new RetryCandidate(rs.getLong("id"), rs.getString("filename"), rs.getString("staging_key")))
                .list();
    }

    @Transactional
    public void recordRetrySuccess(Long jobId, Long itemId, Long documentId) {
        jdbc.sql("""
                        UPDATE import_item
                        SET status = CAST('SUCCESS' AS import_item_status), document_id = :documentId,
                            failure_reason = NULL, retryable = TRUE, staging_key = NULL
                        WHERE id = :id
                        """)
                .param("documentId", documentId).param("id", itemId).update();
        jdbc.sql("UPDATE import_job SET success_count = success_count + 1, fail_count = fail_count - 1 WHERE id = :id")
                .param("id", jobId).update();
    }

    /** 재시도도 실패하면 사유만 갱신한다. fail_count 는 이미 세어져 있으므로 다시 올리지 않는다. */
    @Transactional
    public void recordRetryFailure(Long itemId, String reason, boolean retryable, String newStagingKey) {
        jdbc.sql("""
                        UPDATE import_item
                        SET failure_reason = :reason, retryable = :retryable, staging_key = :stagingKey
                        WHERE id = :id
                        """)
                .param("reason", truncate(reason)).param("retryable", retryable)
                .param("stagingKey", newStagingKey).param("id", itemId)
                .update();
    }

    private String titleFromFilename(String filename) {
        if (filename == null) return "제목 없음";
        int dot = filename.lastIndexOf('.');
        String base = dot < 0 ? filename : filename.substring(0, dot);
        return base.isBlank() ? "제목 없음" : base;
    }

    private String truncate(String reason) {
        if (reason == null) return null;
        return reason.length() > 300 ? reason.substring(0, 300) : reason;
    }
}

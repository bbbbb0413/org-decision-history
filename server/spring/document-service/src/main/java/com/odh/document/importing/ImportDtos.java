package com.odh.document.importing;

import com.odh.document.domain.DocumentType;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/** 일괄 가져오기 요청·응답 타입. */
public final class ImportDtos {

    public record Job(
            UUID jobKey,
            String status,
            int fileCount,
            int successCount,
            int failCount,
            DocumentType defaultDocumentType,
            Long organizationId,
            Long teamId,
            Instant requestedAt,
            Instant finishedAt
    ) {
    }

    public record Item(
            Long id,
            String filename,
            Long documentId,
            String extractedTitle,
            String extractedAuthorName,
            LocalDate extractedAuthoredAt,
            String status,
            String failureReason,
            boolean retryable
    ) {
    }

    public record JobDetail(
            UUID jobKey,
            String status,
            int fileCount,
            int successCount,
            int failCount,
            DocumentType defaultDocumentType,
            Long organizationId,
            Long teamId,
            Instant requestedAt,
            Instant finishedAt,
            List<Item> items
    ) {
        public static JobDetail of(Job job, List<Item> items) {
            return new JobDetail(job.jobKey(), job.status(), job.fileCount(), job.successCount(),
                    job.failCount(), job.defaultDocumentType(), job.organizationId(), job.teamId(),
                    job.requestedAt(), job.finishedAt(), items);
        }
    }

    private ImportDtos() {
    }
}

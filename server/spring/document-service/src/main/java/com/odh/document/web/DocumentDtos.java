package com.odh.document.web;

import com.odh.document.domain.DecisionStatus;
import com.odh.document.domain.DocumentOrigin;
import com.odh.document.domain.DocumentType;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/** 문서 관련 요청·응답 타입. */
public final class DocumentDtos {

    public record CreateRequest(
            @NotBlank(message = "제목은 필수 항목입니다") @Size(max = 200) String title,
            @NotBlank(message = "본문은 필수 항목입니다") String content,
            @NotNull(message = "문서 유형은 필수 항목입니다") DocumentType documentType,
            @NotNull(message = "결정 상태는 필수 항목입니다") DecisionStatus decisionStatus,
            @NotNull(message = "조직은 필수 항목입니다") Long organizationId,
            Long teamId,
            @Size(max = 100) String projectName,
            @NotNull(message = "작성일은 필수 항목입니다") LocalDate authoredAt
    ) {
    }

    public record UpdateRequest(
            @NotBlank(message = "제목은 필수 항목입니다") @Size(max = 200) String title,
            @NotBlank(message = "본문은 필수 항목입니다") String content,
            @NotNull(message = "결정 상태는 필수 항목입니다") DecisionStatus decisionStatus,
            Long teamId,
            @Size(max = 100) String projectName,
            @Size(max = 300) String changeNote,
            @NotNull(message = "현재 버전은 필수 항목입니다") Integer currentVersion
    ) {
    }

    public record Summary(
            Long id,
            String title,
            DocumentType documentType,
            DecisionStatus decisionStatus,
            Long organizationId,
            Long teamId,
            String projectName,
            Long authorId,
            String authorName,
            LocalDate authoredAt,
            int currentVersion,
            Instant updatedAt,
            boolean hasFile
    ) {
    }

    public record Detail(
            Long id,
            String title,
            String content,
            DocumentType documentType,
            DecisionStatus decisionStatus,
            Long organizationId,
            Long teamId,
            String projectName,
            Long authorId,
            String authorName,
            LocalDate authoredAt,
            DocumentOrigin origin,
            int currentVersion,
            Instant createdAt,
            Instant updatedAt,
            FileInfo file
    ) {
    }

    public record FileInfo(
            Long id,
            Long documentId,
            int version,
            String originalFilename,
            String mimeType,
            long sizeBytes,
            String checksumSha256,
            Instant uploadedAt
    ) {
    }

    public record VersionInfo(
            int version,
            String title,
            DecisionStatus decisionStatus,
            String changeNote,
            Long createdBy,
            Instant createdAt,
            Long fileId
    ) {
    }

    /**
     * 그 버전 시점의 본문까지 담는다.
     * 목록({@link VersionInfo})에는 본문을 싣지 않는다 — 버전이 쌓이면 응답이 과하게 커진다.
     */
    public record VersionDetail(
            int version,
            String title,
            DecisionStatus decisionStatus,
            String changeNote,
            Long createdBy,
            Instant createdAt,
            String content
    ) {
    }

    public record DownloadUrl(
            String downloadUrl,
            Instant expiresAt,
            String originalFilename,
            String mimeType,
            long sizeBytes
    ) {
    }

    public record Page(
            List<Summary> content,
            int page,
            int size,
            long totalElements,
            int totalPages,
            Map<String, Integer> typeCounts
    ) {
    }

    private DocumentDtos() {
    }
}

package com.odh.document.web;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;

public final class ConflictDtos {

    private ConflictDtos() {}

    public record ConflictItem(
            long id,
            long sourceDocId,
            String sourceDocTitle,
            long targetDocId,
            String targetDocTitle,
            String sourceClause,
            String targetClause,
            String conflictType,
            double severityScore,
            String reasoning,
            List<String> affectedTeams,
            String status,
            String resolvedBy,
            String resolutionNote,
            Instant resolvedAt,
            Instant createdAt
    ) {}

    /**
     * 상충 상태 변경 요청.
     *
     * <p>의결 사유는 거버넌스 기능의 핵심 산출물이다 — "왜 그렇게 결정했는지"가 없으면
     * 상충을 닫은 기록이 의미를 잃는다. 그래서 사유 없이는 닫을 수 없다.
     * 프론트는 이미 같은 검사를 하지만(ConflictActionModal), 서버가 계약의 주인이다.
     */
    public record ResolveConflictRequest(
            @NotBlank(message = "상태는 필수입니다") String status,
            @NotBlank(message = "조치 사유는 필수입니다")
            @Size(max = 1000, message = "조치 사유는 1000자를 넘을 수 없습니다")
            String resolutionNote
    ) {}
}

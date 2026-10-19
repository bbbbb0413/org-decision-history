package com.odh.document.web;

import com.odh.document.domain.ConflictResolution;
import com.odh.document.domain.ConflictSeverityLevel;
import io.swagger.v3.oas.annotations.media.Schema;
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
            /*
             * 기록 시점 등급. governance-worker 가 기록할 때 정해 저장한 값이라 화면은 점수로 등급을
             * 다시 계산하지 않는다 — 기준이 여러 곳에 있으면 같은 상충이 화면마다 다른 등급으로 보인다.
             */
            ConflictSeverityLevel severityLevel,
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
            // 문자열로 받되(대소문자 무시) 허용값은 ConflictResolution 이다 — 스펙에 enum 으로 드러난다
            @Schema(implementation = ConflictResolution.class)
            @NotBlank(message = "상태는 필수입니다") String status,
            @NotBlank(message = "조치 사유는 필수입니다")
            @Size(max = 1000, message = "조치 사유는 1000자를 넘을 수 없습니다")
            String resolutionNote
    ) {}
}

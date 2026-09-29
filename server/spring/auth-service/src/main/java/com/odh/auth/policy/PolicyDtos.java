package com.odh.auth.policy;

import com.odh.auth.domain.SystemRole;
import jakarta.validation.constraints.NotNull;

import java.time.Instant;
import java.util.List;

/** 접근 정책·감사 요청·응답 타입. */
public final class PolicyDtos {

    public enum SubjectType { USER, ROLE, TEAM }

    public enum ScopeType { ORGANIZATION, TEAM, DOCUMENT }

    public record PolicyRequest(
            @NotNull SubjectType subjectType,
            Long subjectUserId,
            Long subjectTeamId,
            SystemRole subjectRole,
            @NotNull ScopeType scopeType,
            Long scopeOrganizationId,
            Long scopeTeamId,
            Long scopeDocumentId,
            Boolean canRead
    ) {
        public boolean canReadOrDefault() {
            return canRead == null || canRead;
        }

        /** subjectType 과 scopeType 에 맞는 식별자 하나씩만 채워졌는지 확인한다. */
        public String validationError() {
            boolean subjectOk = switch (subjectType) {
                case USER -> subjectUserId != null && subjectTeamId == null && subjectRole == null;
                case TEAM -> subjectTeamId != null && subjectUserId == null && subjectRole == null;
                case ROLE -> subjectRole != null && subjectUserId == null && subjectTeamId == null;
            };
            if (!subjectOk) {
                return "subjectType 에 해당하는 식별자 하나만 채워야 합니다.";
            }
            boolean scopeOk = switch (scopeType) {
                case ORGANIZATION -> scopeOrganizationId != null && scopeTeamId == null && scopeDocumentId == null;
                case TEAM -> scopeTeamId != null && scopeOrganizationId == null && scopeDocumentId == null;
                case DOCUMENT -> scopeDocumentId != null && scopeOrganizationId == null && scopeTeamId == null;
            };
            return scopeOk ? null : "scopeType 에 해당하는 식별자 하나만 채워야 합니다.";
        }
    }

    public record Policy(
            Long id,
            SubjectType subjectType,
            Long subjectUserId,
            Long subjectTeamId,
            SystemRole subjectRole,
            String subjectName,
            ScopeType scopeType,
            Long scopeOrganizationId,
            Long scopeTeamId,
            Long scopeDocumentId,
            String scopeName,
            boolean canRead,
            Long createdBy,
            Instant createdAt,
            Instant revokedAt
    ) {
    }

    public record AuditEntry(
            Long id,
            Long actorId,
            String actorName,
            String action,
            String targetType,
            Long targetId,
            String result,
            String reason,
            String correlationId,
            Instant occurredAt
    ) {
    }

    private PolicyDtos() {
    }
}

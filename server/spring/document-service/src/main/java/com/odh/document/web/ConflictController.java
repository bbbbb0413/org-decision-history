package com.odh.document.web;

import com.odh.common.InternalAuth;
import jakarta.validation.Valid;
import com.odh.common.PermissionScope;
import com.odh.common.internal.AuditClient;
import com.odh.common.internal.PermissionScopeClient;
import com.odh.common.web.PageResponse;
import com.odh.document.service.ConflictQueryService;
import com.odh.document.web.ConflictDtos.ConflictItem;
import com.odh.document.web.ConflictDtos.ResolveConflictRequest;
import org.springframework.web.bind.annotation.*;

/**
 * 상충 목록 조회·해소.
 *
 * 조회는 요청자의 권한 범위 안 상충만 보여준다({@link ConflictQueryService}).
 * 상태 변경(PATCH)은 관리자 역할(SYSTEM_ADMIN/ORG_ADMIN/TEAM_ADMIN 중 하나, 프론트
 * GovernanceDashboard의 isAdmin 판정과 같다)만 할 수 있다 — 일반 사용자는 볼 수는 있어도
 * 상태를 바꿀 수 없다.
 */
@RestController
public class ConflictController {

    private final ConflictQueryService conflictService;
    private final PermissionScopeClient scopes;
    private final AuditClient auditClient;

    public ConflictController(ConflictQueryService conflictService, PermissionScopeClient scopes, AuditClient auditClient) {
        this.conflictService = conflictService;
        this.scopes = scopes;
        this.auditClient = auditClient;
    }

    @GetMapping("/api/governance/conflicts")
    public PageResponse<ConflictItem> list(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "false") boolean mine,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size
    ) {
        // DocumentController 와 같은 이유로 작성자 id 대신 mine 플래그만 받는다.
        return conflictService.listConflicts(status, mine ? userId : null, page, size,
                scopes.scopeOf(userId));
    }

    @GetMapping("/api/governance/conflicts/{id}")
    public ConflictItem getOne(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId, @PathVariable long id) {
        return conflictService.findVisible(id, scopes.scopeOf(userId));
    }

    @PatchMapping("/api/governance/conflicts/{id}")
    public ConflictItem resolve(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestHeader(value = InternalAuth.USER_ROLES_HEADER, required = false) String rolesHeader,
            @PathVariable long id,
            @Valid @RequestBody ResolveConflictRequest request
    ) {
        boolean isAdmin = InternalAuth.parseRoles(rolesHeader).stream().anyMatch(role -> !role.equals("MEMBER"));
        if (!isAdmin) {
            auditClient.record(userId, "CONFLICT_RESOLVE", "DECISION_CONFLICT", id, false,
                    "관리자 권한 없이 상충 상태 변경 시도");
            throw new ForbiddenException();
        }

        PermissionScope scope = scopes.scopeOf(userId);
        ConflictItem resolved = conflictService.resolveConflict(id, request, userId, scope);
        auditClient.record(userId, "CONFLICT_RESOLVE", "DECISION_CONFLICT", id, true,
                "상충 상태 변경: " + resolved.status());
        return resolved;
    }

    public static class ForbiddenException extends RuntimeException {
    }
}

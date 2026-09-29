package com.odh.auth.policy;

import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import com.odh.common.InternalAuth;
import com.odh.common.PermissionScope;
import com.odh.common.web.PageResponse;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
public class PolicyController {

    private final PolicyService policies;
    private final PermissionScopeService scopes;
    private final AuditService audit;

    public PolicyController(PolicyService policies, PermissionScopeService scopes, AuditService audit) {
        this.policies = policies;
        this.scopes = scopes;
        this.audit = audit;
    }

    @GetMapping("/api/access-policies")
    public PageResponse<PolicyDtos.Policy> list(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @RequestParam(defaultValue = "false") boolean includeRevoked,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return policies.list(actorId, includeRevoked, page, size);
    }

    @PostMapping("/api/access-policies")
    public ResponseEntity<?> create(@RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
                                    @Valid @RequestBody PolicyDtos.PolicyRequest request) {
        String error = request.validationError();
        if (error != null) {
            return ResponseEntity.badRequest().body(
                    ErrorResponse.of(ErrorCode.VALIDATION_FAILED, error, null));
        }
        Long id = policies.create(actorId, request);
        return ResponseEntity.status(HttpStatus.CREATED).body(java.util.Map.of("id", id));
    }

    @DeleteMapping("/api/access-policies/{policyId}")
    public ResponseEntity<Void> revoke(@RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
                                       @PathVariable Long policyId) {
        return policies.revoke(actorId, policyId)
                ? ResponseEntity.noContent().build()
                : ResponseEntity.notFound().build();
    }

    @GetMapping("/api/audit-logs")
    public PageResponse<PolicyDtos.AuditEntry> audit(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "30") int size) {
        return policies.auditLogs(actorId, page, size);
    }

    /**
     * 내부 전용. 다른 서비스가 사용자의 읽기 범위를 물어본다.
     * 게이트웨이 라우팅 대상이 아니며 내부 API 키를 가진 호출만 통과한다.
     */
    @GetMapping("/internal/permission-scope/{userId}")
    public PermissionScope permissionScope(@PathVariable Long userId) {
        return scopes.calculate(userId);
    }

    /**
     * 내부 전용. 다른 서비스가 감사 기록을 남긴다.
     * 감사 로그의 소유자를 auth-service 한 곳으로 두어 기록 형식이 갈라지지 않게 한다.
     */
    @PostMapping("/internal/audit")
    public ResponseEntity<Void> recordAudit(@Valid @RequestBody AuditRequest request) {
        audit.record(request.actorId(), request.action(), request.targetType(),
                request.targetId(), request.allowed(), request.reason());
        return ResponseEntity.accepted().build();
    }

    public record AuditRequest(Long actorId,
                               @NotBlank(message = "감사 액션은 필수입니다") String action,
                               String targetType,
                               Long targetId, boolean allowed, String reason) {
    }
}

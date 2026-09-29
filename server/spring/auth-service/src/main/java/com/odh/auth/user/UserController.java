package com.odh.auth.user;

import com.odh.common.InternalAuth;
import com.odh.common.web.PageResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/** 사용자 소속·역할 관리와 조직·팀 조회. */
@RestController
public class UserController {

    private final UserManagementService service;

    public UserController(UserManagementService service) {
        this.service = service;
    }

    @GetMapping("/api/organizations")
    public List<UserDtos.OrganizationRef> listOrganizations(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId) {
        return service.listOrganizations(actorId);
    }

    @PostMapping("/api/organizations")
    public ResponseEntity<UserDtos.OrganizationRef> createOrganization(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @Valid @RequestBody UserDtos.CreateOrganizationRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED).body(service.createOrganization(actorId, request));
    }

    @GetMapping("/api/organizations/{organizationId}/teams")
    public List<UserDtos.TeamRef> listTeams(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long organizationId) {
        return service.listTeams(actorId, organizationId);
    }

    @GetMapping("/api/users")
    public PageResponse<UserDtos.UserSummary> list(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @RequestParam(required = false) String q,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return service.list(actorId, q, page, size);
    }

    @PostMapping("/api/users")
    public ResponseEntity<UserDtos.IssuedUserAccount> issueUserAccount(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @Valid @RequestBody UserDtos.CreateUserAccountRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED).body(service.createUserAccount(actorId, request));
    }

    @GetMapping("/api/users/{userId}")
    public UserDtos.UserDetail get(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long userId) {
        return service.get(actorId, userId);
    }

    @PutMapping("/api/users/{userId}/membership")
    public UserDtos.UserDetail updateMembership(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long userId,
            @Valid @RequestBody UserDtos.MembershipRequest request) {
        return service.updateMembership(actorId, userId, request);
    }

    @PutMapping("/api/users/{userId}/roles")
    public UserDtos.UserDetail updateRoles(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long userId,
            @Valid @RequestBody UserDtos.RolesRequest request) {
        return service.updateRoles(actorId, userId, request);
    }

    /** 계정 활성/비활성 전환. 비활성화하면 그 사용자의 토큰도 함께 무효화된다. */
    @PutMapping("/api/users/{userId}/status")
    public UserDtos.UserDetail updateStatus(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long userId,
            @Valid @RequestBody UserDtos.StatusRequest request) {
        return service.updateStatus(actorId, userId, request);
    }

    /** 임시 비밀번호 재발급. 평문은 이 응답에서만 볼 수 있다. */
    @PostMapping("/api/users/{userId}/password-reset")
    public UserDtos.IssuedTemporaryPassword resetPassword(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long userId) {
        return service.resetPassword(actorId, userId);
    }
}

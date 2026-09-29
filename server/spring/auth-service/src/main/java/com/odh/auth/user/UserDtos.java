package com.odh.auth.user;

import com.odh.auth.domain.SystemRole;
import com.odh.auth.domain.UserStatus;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

/** 사용자 소속·역할 요청·응답 타입. */
public final class UserDtos {

    public record TeamRef(Long id, Long organizationId, String name) {
    }

    public record UserSummary(
            Long id,
            String email,
            String name,
            String status,
            Long organizationId,
            String organizationName,
            List<TeamRef> teams,
            List<SystemRole> roles
    ) {
    }

    public record UserDetail(
            Long id,
            String email,
            String name,
            String status,
            Long organizationId,
            String organizationName,
            List<TeamRef> teams,
            List<SystemRole> roles,
            Instant createdAt,
            Instant updatedAt
    ) {
    }

    /** teamIds 는 전체 교체 방식이다. */
    public record MembershipRequest(
            @NotNull Long organizationId,
            @NotNull List<Long> teamIds
    ) {
    }

    public record RolesRequest(
            @NotEmpty List<SystemRole> roles
    ) {
    }

    /** 계정 활성/비활성 전환. */
    public record StatusRequest(
            @NotNull UserStatus status
    ) {
    }

    /**
     * 관리자가 재발급한 임시 비밀번호.
     * temporaryPassword 는 이 응답에서만 노출되고 서버에는 해시로만 저장된다.
     */
    public record IssuedTemporaryPassword(
            Long userId,
            String email,
            String temporaryPassword
    ) {
    }

    public record OrganizationRef(Long id, String name) {
    }

    public record CreateOrganizationRequest(
            @NotBlank @Size(max = 100) String name
    ) {
    }

    public record CreateUserAccountRequest(
            @NotBlank @Email @Size(max = 255) String email,
            @NotBlank @Size(max = 100) String name,
            @NotNull Long organizationId,
            List<Long> teamIds,
            @NotEmpty List<SystemRole> roles
    ) {
    }

    /**
     * temporaryPassword 는 이 응답에서만 노출되고 서버에는 해시로만 저장된다.
     */
    public record IssuedUserAccount(
            Long id,
            String email,
            String name,
            String status,
            Long organizationId,
            String organizationName,
            List<TeamRef> teams,
            List<SystemRole> roles,
            Instant createdAt,
            Instant updatedAt,
            String temporaryPassword
    ) {
    }

    private UserDtos() {
    }
}

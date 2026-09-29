package com.odh.auth.web;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.List;

/** 인증 관련 요청·응답 타입. */
public final class AuthDtos {

    public record LoginRequest(
            @NotBlank @Email @Size(max = 255) String email,
            @NotBlank @Size(min = 8, max = 100) String password
    ) {
    }

    public record RefreshRequest(@NotBlank String refreshToken) {
    }

    /** 본인 비밀번호 변경. 길이 하한은 로그인 요청과 같게 맞춘다. */
    public record ChangePasswordRequest(
            @NotBlank String currentPassword,
            @NotBlank @Size(min = 8, max = 100, message = "새 비밀번호는 8자 이상이어야 합니다") String newPassword
    ) {
    }

    public record TokenResponse(
            String accessToken,
            String refreshToken,
            long expiresIn,
            CurrentUser user
    ) {
    }

    public record TeamDto(Long id, Long organizationId, String name) {
    }

    public record CurrentUser(
            Long id,
            String email,
            String name,
            String status,
            Long organizationId,
            String organizationName,
            List<TeamDto> teams,
            List<String> roles
    ) {
    }

    private AuthDtos() {
    }
}

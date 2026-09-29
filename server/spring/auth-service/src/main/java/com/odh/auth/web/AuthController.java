package com.odh.auth.web;

import com.odh.auth.jwt.JwtKeyProvider;
import com.odh.common.InternalAuth;
import jakarta.validation.Valid;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
public class AuthController {

    private final AuthService authService;
    private final JwtKeyProvider keyProvider;

    public AuthController(AuthService authService, JwtKeyProvider keyProvider) {
        this.authService = authService;
        this.keyProvider = keyProvider;
    }

    @PostMapping("/api/auth/login")
    public AuthDtos.TokenResponse login(@Valid @RequestBody AuthDtos.LoginRequest request) {
        return authService.login(request.email(), request.password());
    }

    /**
     * 게이트웨이가 재발급 토큰의 서명과 만료를 먼저 검증하고 사용자 식별자를 헤더로 넘긴다.
     *
     * 회전(한 번 쓴 토큰은 재사용 불가)을 하려면 "이 토큰"을 식별해야 해서 `jti` 가 필요하고,
     * 그 값은 헤더에 없다. 게이트웨이가 `Authorization` 을 지우지 않고 넘기므로 원본을 함께 받는다
     * (근거는 {@link com.odh.auth.jwt.RefreshTokenReader}).
     */
    @PostMapping("/api/auth/refresh")
    public AuthDtos.TokenResponse refresh(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestHeader(value = HttpHeaders.AUTHORIZATION, required = false) String authorization) {
        return authService.reissue(userId, authorization);
    }

    @GetMapping("/api/auth/me")
    public AuthDtos.CurrentUser me(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId) {
        return authService.currentUser(userId);
    }

    /**
     * 이 사용자가 지금까지 받은 모든 토큰(액세스·재발급)을 무효화한다.
     */
    @PostMapping("/api/auth/logout")
    public ResponseEntity<Void> logout(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId) {
        authService.logout(userId);
        return ResponseEntity.noContent().build();
    }

    /**
     * 본인 비밀번호 변경.
     * 성공하면 기존 토큰이 전부 무효화되므로(호출한 본인 것 포함) 프론트는 다시 로그인시킨다.
     */
    @PostMapping("/api/auth/password")
    public ResponseEntity<Void> changePassword(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @Valid @RequestBody AuthDtos.ChangePasswordRequest request) {
        authService.changePassword(userId, request.currentPassword(), request.newPassword());
        return ResponseEntity.noContent().build();
    }

    /**
     * 공개 JWKS. 게이트웨이가 이 경로에서 공개키를 받아 토큰 서명을 검증한다.
     * 인증 없이 접근할 수 있어야 하므로 게이트웨이의 공개 경로 목록에 포함한다.
     */
    @GetMapping("/.well-known/jwks.json")
    public ResponseEntity<Map<String, Object>> jwks() {
        return ResponseEntity.ok(keyProvider.publicJwkSet());
    }
}

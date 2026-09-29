package com.odh.auth.web;

import com.odh.auth.domain.AppUser;
import com.odh.auth.domain.SystemRole;
import com.odh.auth.domain.UserRole;
import com.odh.auth.jwt.RefreshTokenReader;
import com.odh.auth.jwt.RefreshTokenRotation;
import com.odh.auth.jwt.TokenRevoker;
import com.odh.auth.jwt.TokenService;
import com.odh.auth.policy.AuditService;
import com.odh.auth.repository.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@Transactional(readOnly = true)
public class AuthService {

    private final AppUserRepository users;
    private final OrganizationRepository organizations;
    private final TeamRepository teams;
    private final UserRoleRepository roles;
    private final TokenService tokenService;
    private final TokenRevoker tokenRevoker;
    private final RefreshTokenReader refreshTokenReader;
    private final RefreshTokenRotation refreshRotation;
    private final AuditService audit;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();

    private static final Logger log = LoggerFactory.getLogger(AuthService.class);

    public AuthService(AppUserRepository users,
                       OrganizationRepository organizations,
                       TeamRepository teams,
                       UserRoleRepository roles,
                       TokenService tokenService,
                       TokenRevoker tokenRevoker,
                       RefreshTokenReader refreshTokenReader,
                       RefreshTokenRotation refreshRotation,
                       AuditService audit) {
        this.users = users;
        this.organizations = organizations;
        this.teams = teams;
        this.roles = roles;
        this.tokenService = tokenService;
        this.tokenRevoker = tokenRevoker;
        this.refreshTokenReader = refreshTokenReader;
        this.refreshRotation = refreshRotation;
        this.audit = audit;
    }

    /**
     * 인증에 실패하면 이메일이 없는 경우와 비밀번호가 틀린 경우를 구분하지 않는다.
     * 구분해서 알려주면 어떤 이메일이 가입되어 있는지 노출된다.
     */
    public AuthDtos.TokenResponse login(String email, String rawPassword) {
        AppUser user = users.findByEmail(email)
                .filter(u -> passwordEncoder.matches(rawPassword, u.getPasswordHash()))
                .filter(AppUser::isActive)
                .orElseThrow(InvalidCredentialsException::new);

        List<SystemRole> userRoles = rolesOf(user.getId());
        return new AuthDtos.TokenResponse(
                tokenService.issueAccessToken(user.getId(), user.getEmail(), userRoles),
                tokenService.issueRefreshToken(user.getId()),
                tokenService.accessTtlSeconds(),
                currentUser(user.getId())
        );
    }

    /**
     * 재발급. 게이트웨이가 서명·만료·로그아웃 워터마크를 이미 검증했고, 여기서는 **회전**을 한다.
     *
     * 쓴 토큰은 다시 쓸 수 없다. 그렇지 않으면 탈취된 토큰으로 만료(14일)까지 무제한
     * 재발급이 가능하다.
     *
     * 유예 구간 밖에서 재사용이 나오면 그 토큰만 막는 것으로 부족하다 — 같은 토큰을 둘이
     * 들고 있다는 뜻이므로 그 사용자의 세션을 전부 끊는다. 판단 근거는
     * {@link RefreshTokenRotation} 에 적었다.
     *
     * @param authorization 원본 `Authorization` 헤더. 게이트웨이가 지우지 않고 그대로 넘긴다
     */
    public AuthDtos.TokenResponse reissue(Long userId, String authorization) {
        RefreshTokenReader.RefreshToken token = refreshTokenReader.read(authorization, userId);

        RefreshTokenRotation.Outcome outcome = refreshRotation.claim(token.jti(), token.expiresAt());
        if (outcome == RefreshTokenRotation.Outcome.REUSE_DETECTED) {
            // 사용자에게는 사유를 구분해 주지 않는다(로그인 실패와 같은 이유). 로그에만 남긴다.
            log.warn("재발급 토큰 재사용 감지 — 전 세션을 무효화한다 userId={} jti={}", userId, token.jti());
            tokenRevoker.revokeAll(userId);
            audit.record(userId, "TOKEN_REUSE_DETECTED", "USER", userId, false,
                    "재발급 토큰 재사용이 감지되어 전 세션을 무효화했습니다");
            // InvalidCredentialsException 이 아니다 — 그 예외는 "이메일 또는 비밀번호가 올바르지
            // 않습니다"로 응답하는데, 토큰 재발급 중에 그 문구가 뜨면 사용자가 무엇을 잘못했는지
            // 오해한다. 폐기된 토큰으로 온 요청(게이트웨이 워터마크)과 같은 응답으로 맞춘다.
            throw new RefreshTokenReader.InvalidRefreshTokenException("재사용 감지로 세션을 무효화했다");
        }
        if (outcome == RefreshTokenRotation.Outcome.GRACE_REPLAY) {
            // 끊지 않는다. 탭 동시 재발급이 흔해서 이걸 사고로 보면 정상 사용자가 로그아웃된다.
            log.debug("유예 구간 안의 재발급 토큰 재사용 — 통과시킨다 userId={}", userId);
        }

        AppUser user = users.findById(userId)
                .filter(AppUser::isActive)
                .orElseThrow(InvalidCredentialsException::new);

        List<SystemRole> userRoles = rolesOf(user.getId());
        return new AuthDtos.TokenResponse(
                tokenService.issueAccessToken(user.getId(), user.getEmail(), userRoles),
                tokenService.issueRefreshToken(user.getId()),
                tokenService.accessTtlSeconds(),
                currentUser(user.getId())
        );
    }

    /**
     * 이 사용자에게 지금까지 발급된 액세스·재발급 토큰을 전부 무효화한다.
     * 개별 기기가 아니라 모든 세션을 끝낸다 — 서버가 발급한 토큰 목록을 따로 저장하지
     * 않아 "이 토큰만" 지울 방법이 없기 때문이다({@link TokenRevocation}).
     */
    public void logout(Long userId) {
        tokenRevoker.revokeAll(userId);
    }

    /**
     * 본인 비밀번호 변경.
     *
     * 성공하면 이 사용자의 기존 토큰을 전부 무효화한다 — 비밀번호를 바꾸는 흔한 이유가
     * "탈취가 의심된다"인데, 그때 기존 세션이 살아 있으면 바꾼 의미가 없다. 호출한 본인의
     * 토큰도 함께 끊기므로 프론트는 성공 후 다시 로그인시킨다.
     *
     * 현재 비밀번호가 틀리면 로그인과 같은 이유로 사유를 구분해 주지 않는다.
     */
    @Transactional
    public void changePassword(Long userId, String currentPassword, String newPassword) {
        AppUser user = users.findById(userId)
                .filter(AppUser::isActive)
                .orElseThrow(InvalidCredentialsException::new);

        if (!passwordEncoder.matches(currentPassword, user.getPasswordHash())) {
            audit.record(userId, "PASSWORD_CHANGE", "USER", userId, false, "현재 비밀번호 불일치");
            throw new InvalidCredentialsException();
        }
        if (passwordEncoder.matches(newPassword, user.getPasswordHash())) {
            throw new SamePasswordException();
        }

        users.updatePasswordHash(userId, passwordEncoder.encode(newPassword));
        tokenRevoker.revokeAll(userId);
        audit.record(userId, "PASSWORD_CHANGE", "USER", userId, true, "본인 비밀번호 변경");
    }

    public AuthDtos.CurrentUser currentUser(Long userId) {
        AppUser user = users.findById(userId).orElseThrow(UserNotFoundException::new);

        String organizationName = user.getOrganizationId() == null ? null
                : organizations.findById(user.getOrganizationId())
                        .map(o -> o.getName())
                        .orElse(null);

        List<AuthDtos.TeamDto> teamDtos = teams.findByUserId(userId).stream()
                .map(t -> new AuthDtos.TeamDto(t.getId(), t.getOrganizationId(), t.getName()))
                .toList();

        return new AuthDtos.CurrentUser(
                user.getId(),
                user.getEmail(),
                user.getName(),
                user.getStatus().name(),
                user.getOrganizationId(),
                organizationName,
                teamDtos,
                rolesOf(userId).stream().map(Enum::name).toList()
        );
    }

    private List<SystemRole> rolesOf(Long userId) {
        return roles.findByUserId(userId).stream().map(UserRole::getRole).toList();
    }

    public static class InvalidCredentialsException extends RuntimeException {
    }

    /** 새 비밀번호가 지금 쓰는 것과 같다. 바뀐 게 없는데 바뀐 줄 알고 넘어가지 않도록 막는다. */
    public static class SamePasswordException extends RuntimeException {
    }

    public static class UserNotFoundException extends RuntimeException {
    }
}

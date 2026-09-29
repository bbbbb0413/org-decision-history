package com.odh.auth.user;

import com.odh.auth.policy.PolicyService;
import com.odh.auth.web.AuthService;
import com.odh.common.TokenRevocation;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import com.odh.auth.domain.UserStatus;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.api.Assertions.assertThatCode;

/**
 * 비밀번호 변경·재발급과 계정 활성/비활성.
 *
 * 이 테스트는 auth-service 에서 처음으로 Redis 컨테이너를 띄운다 — 세 조작 모두 성공 시
 * 토큰 폐기 워터마크({@link TokenRevocation})를 찍는 게 핵심 동작이라, Redis 없이는
 * "세션이 실제로 끊기는가"를 검증할 수 없다.
 *
 * 픽스처: 플랫폼본부(org1)에 결제팀(team1), 보안본부(org2)에 정보보안팀(team2).
 * admin=SYSTEM_ADMIN(org1), lead=TEAM_ADMIN(org1, team1), member=MEMBER(org1, team1),
 * outsider=MEMBER(org2, team2).
 */
@SpringBootTest
@Testcontainers
class AccountSecurityTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7.4-alpine").withExposedPorts(6379);

    @DynamicPropertySource
    static void properties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
    }

    private static final String PASSWORD = "password123";

    @Autowired
    private UserManagementService userService;

    @Autowired
    private AuthService authService;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private StringRedisTemplate redisTemplate;

    private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();

    private long adminId;
    private long leadId;
    private long memberId;
    private long outsiderId;

    @BeforeEach
    void seed() {
        jdbc.sql("TRUNCATE audit_log, access_policy, user_role, user_team, app_user, team, organization RESTART IDENTITY CASCADE").update();

        long org1 = insertOrg("플랫폼본부");
        long org2 = insertOrg("보안본부");
        long team1 = insertTeam(org1, "결제팀");
        long team2 = insertTeam(org2, "정보보안팀");

        adminId = insertUser("admin@t.com", org1);
        leadId = insertUser("lead@t.com", org1);
        memberId = insertUser("member@t.com", org1);
        outsiderId = insertUser("outsider@t.com", org2);

        insertRole(adminId, "SYSTEM_ADMIN");
        insertRole(leadId, "TEAM_ADMIN");
        insertRole(memberId, "MEMBER");
        insertRole(outsiderId, "MEMBER");

        insertMembership(leadId, team1);
        insertMembership(memberId, team1);
        insertMembership(adminId, team1);
        insertMembership(outsiderId, team2);

        redisTemplate.delete(java.util.List.of(
                watermarkKey(adminId), watermarkKey(leadId), watermarkKey(memberId), watermarkKey(outsiderId)));
    }

    // ── 본인 비밀번호 변경 ────────────────────────────

    @Test
    void changePassword_replacesPasswordAndEndsAllSessions() {
        authService.changePassword(memberId, PASSWORD, "newpassword456");

        // 새 비밀번호로만 로그인된다
        assertThatCode(() -> authService.login("member@t.com", "newpassword456")).doesNotThrowAnyException();
        assertThatThrownBy(() -> authService.login("member@t.com", PASSWORD))
                .isInstanceOf(AuthService.InvalidCredentialsException.class);
        // 바꾸기 전에 발급된 토큰은 전부 무효다
        assertThat(redisTemplate.opsForValue().get(watermarkKey(memberId))).isNotNull();
    }

    @Test
    void changePassword_withWrongCurrentPassword_isRejectedAndKeepsSessions() {
        assertThatThrownBy(() -> authService.changePassword(memberId, "wrong-password", "newpassword456"))
                .isInstanceOf(AuthService.InvalidCredentialsException.class);

        assertThatCode(() -> authService.login("member@t.com", PASSWORD)).doesNotThrowAnyException();
        assertThat(redisTemplate.opsForValue().get(watermarkKey(memberId))).isNull();
    }

    @Test
    void changePassword_toTheSameValue_isRejected() {
        assertThatThrownBy(() -> authService.changePassword(memberId, PASSWORD, PASSWORD))
                .isInstanceOf(AuthService.SamePasswordException.class);
    }

    @Test
    void changePassword_onDisabledAccount_isRejected() {
        userService.updateStatus(adminId, memberId, new UserDtos.StatusRequest(UserStatus.DISABLED));

        assertThatThrownBy(() -> authService.changePassword(memberId, PASSWORD, "newpassword456"))
                .isInstanceOf(AuthService.InvalidCredentialsException.class);
    }

    // ── 계정 활성/비활성 ──────────────────────────────

    @Test
    void disablingUser_blocksLoginAndEndsAllSessions() {
        var updated = userService.updateStatus(adminId, memberId, new UserDtos.StatusRequest(UserStatus.DISABLED));

        assertThat(updated.status()).isEqualTo("DISABLED");
        assertThatThrownBy(() -> authService.login("member@t.com", PASSWORD))
                .isInstanceOf(AuthService.InvalidCredentialsException.class);
        // 로그인만 막으면 이미 발급된 액세스 토큰이 만료될 때까지 살아 있다 — 워터마크로 즉시 끊는다
        assertThat(redisTemplate.opsForValue().get(watermarkKey(memberId))).isNotNull();
    }

    @Test
    void reEnablingUser_restoresLogin() {
        userService.updateStatus(adminId, memberId, new UserDtos.StatusRequest(UserStatus.DISABLED));
        userService.updateStatus(adminId, memberId, new UserDtos.StatusRequest(UserStatus.ACTIVE));

        assertThatCode(() -> authService.login("member@t.com", PASSWORD)).doesNotThrowAnyException();
    }

    @Test
    void changingOwnStatus_isRejected() {
        // 자기 계정을 잠그면 스스로 되돌릴 수 없다
        assertThatThrownBy(() -> userService.updateStatus(adminId, adminId, new UserDtos.StatusRequest(UserStatus.DISABLED)))
                .isInstanceOf(UserManagementService.ValidationException.class);
    }

    @Test
    void teamAdmin_cannotChangeStatusOfUserOutsideScope() {
        assertThatThrownBy(() -> userService.updateStatus(leadId, outsiderId, new UserDtos.StatusRequest(UserStatus.DISABLED)))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    /**
     * 권한 상승 방지: 팀 관리자는 같은 팀의 시스템 관리자 역할을 건드릴 수 없는데(UserManagementServiceTest),
     * 계정을 잠그는 걸로 우회할 수 있으면 같은 효과가 난다.
     */
    @Test
    void teamAdmin_cannotDisableSystemAdminInOwnTeam() {
        assertThatThrownBy(() -> userService.updateStatus(leadId, adminId, new UserDtos.StatusRequest(UserStatus.DISABLED)))
                .isInstanceOf(PolicyService.ScopeViolationException.class);

        assertThatCode(() -> authService.login("admin@t.com", PASSWORD)).doesNotThrowAnyException();
    }

    // ── 관리자 임시 비밀번호 재발급 ───────────────────

    @Test
    void resetPassword_issuesWorkingTemporaryPasswordAndEndsAllSessions() {
        var issued = userService.resetPassword(adminId, memberId);

        assertThat(issued.email()).isEqualTo("member@t.com");
        assertThat(issued.temporaryPassword()).isNotBlank();
        assertThatCode(() -> authService.login("member@t.com", issued.temporaryPassword())).doesNotThrowAnyException();
        assertThatThrownBy(() -> authService.login("member@t.com", PASSWORD))
                .isInstanceOf(AuthService.InvalidCredentialsException.class);
        assertThat(redisTemplate.opsForValue().get(watermarkKey(memberId))).isNotNull();
    }

    @Test
    void teamAdmin_cannotResetPasswordOfSystemAdmin() {
        // 비밀번호를 갈아치우면 그 계정을 그대로 가로챌 수 있다 — 역할 변경과 같은 상한을 적용해야 한다
        assertThatThrownBy(() -> userService.resetPassword(leadId, adminId))
                .isInstanceOf(PolicyService.ScopeViolationException.class);

        assertThatCode(() -> authService.login("admin@t.com", PASSWORD)).doesNotThrowAnyException();
    }

    @Test
    void teamAdmin_canResetPasswordOfTeammate() {
        var issued = userService.resetPassword(leadId, memberId);

        assertThatCode(() -> authService.login("member@t.com", issued.temporaryPassword())).doesNotThrowAnyException();
    }

    // ── 헬퍼 ──────────────────────────────────────────

    private static String watermarkKey(long userId) {
        return TokenRevocation.watermarkKey(String.valueOf(userId));
    }

    private long insertOrg(String name) {
        return jdbc.sql("INSERT INTO organization (name) VALUES (:name) RETURNING id")
                .param("name", name).query(Long.class).single();
    }

    private long insertTeam(long orgId, String name) {
        return jdbc.sql("INSERT INTO team (organization_id, name) VALUES (:org, :name) RETURNING id")
                .param("org", orgId).param("name", name).query(Long.class).single();
    }

    private long insertUser(String email, long orgId) {
        return jdbc.sql("""
                        INSERT INTO app_user (email, password_hash, name, organization_id, status)
                        VALUES (:email, :hash, :email, :org, CAST('ACTIVE' AS user_status)) RETURNING id
                        """)
                .param("email", email).param("hash", encoder.encode(PASSWORD)).param("org", orgId)
                .query(Long.class).single();
    }

    private void insertRole(long userId, String role) {
        jdbc.sql("INSERT INTO user_role (user_id, role) VALUES (:userId, CAST(:role AS system_role))")
                .param("userId", userId).param("role", role).update();
    }

    private void insertMembership(long userId, long teamId) {
        jdbc.sql("INSERT INTO user_team (user_id, team_id) VALUES (:userId, :teamId) ON CONFLICT DO NOTHING")
                .param("userId", userId).param("teamId", teamId).update();
    }
}

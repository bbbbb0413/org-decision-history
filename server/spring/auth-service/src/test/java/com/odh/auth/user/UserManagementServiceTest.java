package com.odh.auth.user;

import com.odh.auth.domain.SystemRole;
import com.odh.auth.policy.PolicyService;
import com.odh.auth.web.AuthService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 사용자 소속·역할 관리의 관리 범위 판정.
 *
 * 실제 Postgres 로 검증한다 — 네이티브 enum 캐스팅과 크로스 테이블 스코프 쿼리가
 * 많아 H2 같은 대체 DB 로는 같은 SQL 방언 문제를 재현하지 못한다.
 *
 * 픽스처: 플랫폼본부(org1)에 결제팀(team1)·인증팀(team2), 보안본부(org2)에
 * 정보보안팀(team3). admin=SYSTEM_ADMIN(org1), lead=TEAM_ADMIN(org1, team1+2),
 * orgAdmin=ORG_ADMIN(org1), member=MEMBER(org2, team3).
 */
@SpringBootTest
@Testcontainers
class UserManagementServiceTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private UserManagementService service;

    @Autowired
    private JdbcClient jdbc;

    private long org1;
    private long org2;
    private long team1;
    private long team2;
    private long team3;
    private long team4;
    private long adminId;
    private long leadId;
    private long orgAdminId;
    private long memberId;

    @BeforeEach
    void seed() {
        // 스코프 판정이 이전 테스트 데이터에 영향받지 않도록 매번 비우고 새로 심는다
        jdbc.sql("TRUNCATE access_policy, user_role, user_team, app_user, team, organization RESTART IDENTITY CASCADE").update();

        org1 = insertOrg("플랫폼본부");
        org2 = insertOrg("보안본부");
        team1 = insertTeam(org1, "결제팀");
        team2 = insertTeam(org1, "인증팀");
        team3 = insertTeam(org2, "정보보안팀");
        team4 = insertTeam(org1, "디자인팀"); // lead 가 관리하지 않는, 플랫폼본부 소속의 또 다른 팀

        adminId = insertUser("admin@t.com", "김하늘", org1);
        leadId = insertUser("lead@t.com", "박서준", org1);
        orgAdminId = insertUser("orgadmin@t.com", "최조직", org1);
        memberId = insertUser("member@t.com", "이도윤", org2);

        insertRole(adminId, "SYSTEM_ADMIN");
        insertRole(leadId, "TEAM_ADMIN");
        insertRole(orgAdminId, "ORG_ADMIN");
        insertRole(memberId, "MEMBER");

        insertMembership(leadId, team1);
        insertMembership(leadId, team2);
        insertMembership(memberId, team3);
    }

    // ── 조회 범위 ──────────────────────────────────────

    @Test
    void systemAdmin_seesAllUsers() {
        var page = service.list(adminId, null, 0, 20);
        assertThat(page.totalElements()).isEqualTo(4);
    }

    @Test
    void teamAdmin_seesOnlyUsersSharingATeam() {
        // lead 는 team1·team2 소속이고, admin 도 결제팀에 있다고 가정하지 않았으니
        // lead 범위에는 자기 자신만 있어야 한다
        var page = service.list(leadId, null, 0, 20);
        assertThat(page.content()).extracting(UserDtos.UserSummary::id).containsExactly(leadId);
    }

    @Test
    void teamAdmin_canSeeTeammateOutsideTheAdminItself() {
        insertMembership(adminId, team1); // admin 도 이제 결제팀 소속
        var page = service.list(leadId, null, 0, 20);
        assertThat(page.content()).extracting(UserDtos.UserSummary::id).containsExactlyInAnyOrder(leadId, adminId);
    }

    @Test
    void memberWithNoAdminRole_isRejected() {
        assertThatThrownBy(() -> service.list(memberId, null, 0, 20))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    @Test
    void teamAdmin_gettingOutOfScopeUser_isRejected() {
        assertThatThrownBy(() -> service.get(leadId, memberId))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    @Test
    void unknownUser_isNotFound() {
        assertThatThrownBy(() -> service.get(adminId, 9999L))
                .isInstanceOf(AuthService.UserNotFoundException.class);
    }

    // ── 역할 변경과 권한 상승 방지 ──────────────────────

    @Test
    void teamAdmin_cannotGrantOrgAdminRole() {
        insertMembership(memberId, team1); // 범위 안으로 들어오게
        assertThatThrownBy(() -> service.updateRoles(leadId, memberId, new UserDtos.RolesRequest(List.of(SystemRole.ORG_ADMIN))))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    /**
     * 회귀 테스트: TEAM_ADMIN 이 같은 팀의 SYSTEM_ADMIN 역할을 "전체 교체" 요청으로
     * 지워버릴 수 있던 권한 상승 취약점. 요청 역할만 검사해서는 못 막고, 대상이 이미
     * 가진 역할도 상한 안에 있는지 확인해야 막힌다.
     */
    @Test
    void teamAdmin_cannotStripSystemAdminRoleOfATeammate() {
        insertMembership(adminId, team1); // admin 이 lead 와 같은 팀에 있다

        assertThatThrownBy(() -> service.updateRoles(leadId, adminId, new UserDtos.RolesRequest(List.of(SystemRole.MEMBER))))
                .isInstanceOf(PolicyService.ScopeViolationException.class);

        // 거부됐으니 admin 은 여전히 SYSTEM_ADMIN 이어야 한다
        var stillAdmin = service.get(adminId, adminId);
        assertThat(stillAdmin.roles()).containsExactly(SystemRole.SYSTEM_ADMIN);
    }

    @Test
    void systemAdmin_canGrantAnyRoleIncludingSystemAdmin() {
        var updated = service.updateRoles(adminId, memberId, new UserDtos.RolesRequest(List.of(SystemRole.SYSTEM_ADMIN)));
        assertThat(updated.roles()).containsExactly(SystemRole.SYSTEM_ADMIN);
    }

    // ── 소속 변경 ──────────────────────────────────────

    @Test
    void orgAdmin_cannotMoveUserToAnotherOrganization() {
        insertMembership(memberId, team1);
        jdbc.sql("UPDATE app_user SET organization_id = :org WHERE id = :id")
                .param("org", org1).param("id", memberId).update();

        assertThatThrownBy(() -> service.updateMembership(orgAdminId, memberId,
                new UserDtos.MembershipRequest(org2, List.of())))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    @Test
    void teamAdmin_membershipUpdate_preservesTeamsOutsideOwnScope() {
        // member 를 플랫폼본부(org1)로 옮기고, lead 와 공유하는 team1(범위 판정용)과
        // lead 가 관리하지 않는 team4 에 함께 소속시킨다. TEAM_ADMIN 은 조직을 바꿀 수
        // 없고 요청 팀도 같은 조직이어야 하므로, lead 가 team1 만 다시 요청해도 team4
        // 소속은 "전체 교체"로 지워지면 안 된다.
        jdbc.sql("UPDATE app_user SET organization_id = :org WHERE id = :id")
                .param("org", org1).param("id", memberId).update();
        jdbc.sql("DELETE FROM user_team WHERE user_id = :id").param("id", memberId).update();
        insertMembership(memberId, team1);
        insertMembership(memberId, team4);

        var updated = service.updateMembership(leadId, memberId,
                new UserDtos.MembershipRequest(org1, List.of(team1)));

        assertThat(updated.teams()).extracting(UserDtos.TeamRef::id).containsExactlyInAnyOrder(team1, team4);
    }

    // ── 헬퍼 ──────────────────────────────────────────

    private long insertOrg(String name) {
        return jdbc.sql("INSERT INTO organization (name) VALUES (:name) RETURNING id")
                .param("name", name).query(Long.class).single();
    }

    private long insertTeam(long orgId, String name) {
        return jdbc.sql("INSERT INTO team (organization_id, name) VALUES (:org, :name) RETURNING id")
                .param("org", orgId).param("name", name).query(Long.class).single();
    }

    private long insertUser(String email, String name, long orgId) {
        return jdbc.sql("""
                        INSERT INTO app_user (email, password_hash, name, organization_id, status)
                        VALUES (:email, 'x', :name, :org, CAST('ACTIVE' AS user_status)) RETURNING id
                        """)
                .param("email", email).param("name", name).param("org", orgId)
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

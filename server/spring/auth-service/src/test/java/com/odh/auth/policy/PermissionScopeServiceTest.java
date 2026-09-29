package com.odh.auth.policy;

import com.odh.common.PermissionScope;
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

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 사용자 읽기 범위 계산 — 색인·검색·다운로드가 함께 쓰는 핵심 로직.
 *
 * 실제 Postgres 로 검증한다 — 네이티브 enum 캐스팅과 access_policy 의 서브젝트/스코프 체크
 * 제약이 많아 H2 로는 같은 SQL 방언을 재현하지 못한다.
 */
@SpringBootTest
@Testcontainers
class PermissionScopeServiceTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private PermissionScopeService service;

    @Autowired
    private JdbcClient jdbc;

    private long org1;
    private long team1;
    private long userId;

    @BeforeEach
    void seed() {
        jdbc.sql("TRUNCATE access_policy, user_role, user_team, app_user, team, organization RESTART IDENTITY CASCADE").update();

        org1 = insertOrg("플랫폼본부");
        team1 = insertTeam(org1, "결제팀");
        userId = insertUser("user@t.com", "이도윤", org1);
    }

    @Test
    void userWithNoPolicies_hasEmptyScope() {
        PermissionScope scope = service.calculate(userId);

        assertThat(scope.isEmpty()).isTrue();
        assertThat(scope.systemAdmin()).isFalse();
    }

    @Test
    void directUserPolicy_grantsOrganizationScope() {
        insertUserOrgPolicy(userId, org1, true);

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.organizationIds()).containsExactly(org1);
        assertThat(scope.isEmpty()).isFalse();
    }

    @Test
    void teamPolicy_appliesToAllMembersOfTheTeam() {
        insertMembership(userId, team1);
        insertTeamPolicy(team1, org1, true);

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.organizationIds()).containsExactly(org1);
    }

    @Test
    void teamPolicy_doesNotApplyToNonMembers() {
        // 정책은 team1 에 걸려 있지만 user 는 그 팀 소속이 아니다
        insertTeamPolicy(team1, org1, true);

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.isEmpty()).isTrue();
    }

    @Test
    void rolePolicy_appliesToAllUsersWithThatRole() {
        insertRole(userId, "TEAM_ADMIN");
        insertRolePolicy("TEAM_ADMIN", org1, true);

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.organizationIds()).containsExactly(org1);
        assertThat(scope.systemAdmin()).isFalse();
    }

    @Test
    void systemAdminRole_setsFlagIndependentlyOfPolicies() {
        // systemAdmin 플래그는 역할만으로 결정된다 — 정책이 하나도 없어도 켜져야 한다
        insertRole(userId, "SYSTEM_ADMIN");

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.systemAdmin()).isTrue();
        assertThat(scope.organizationIds()).isEmpty();
    }

    @Test
    void denyPolicy_overridesAllowForTheSameDocument() {
        insertUserDocPolicy(userId, 100L, true);
        insertUserDocPolicy(userId, 100L, false);

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.documentIds()).doesNotContain(100L);
        assertThat(scope.deniedDocumentIds()).containsExactly(100L);
    }

    @Test
    void denyPolicy_doesNotAffectOtherDocuments() {
        insertUserDocPolicy(userId, 100L, true);
        insertUserDocPolicy(userId, 200L, true);
        insertUserDocPolicy(userId, 200L, false);

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.documentIds()).containsExactly(100L);
        assertThat(scope.deniedDocumentIds()).containsExactly(200L);
    }

    @Test
    void revokedPolicy_isIgnored() {
        long policyId = insertUserOrgPolicy(userId, org1, true);
        jdbc.sql("UPDATE access_policy SET revoked_at = now() WHERE id = :id")
                .param("id", policyId).update();

        PermissionScope scope = service.calculate(userId);

        assertThat(scope.isEmpty()).isTrue();
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

    private long insertUserOrgPolicy(long userId, long orgId, boolean canRead) {
        return jdbc.sql("""
                        INSERT INTO access_policy (subject_type, subject_user_id, scope_type, scope_organization_id, can_read)
                        VALUES ('USER', :userId, 'ORGANIZATION', :orgId, :canRead) RETURNING id
                        """)
                .param("userId", userId).param("orgId", orgId).param("canRead", canRead)
                .query(Long.class).single();
    }

    private void insertTeamPolicy(long teamId, long orgId, boolean canRead) {
        jdbc.sql("""
                        INSERT INTO access_policy (subject_type, subject_team_id, scope_type, scope_organization_id, can_read)
                        VALUES ('TEAM', :teamId, 'ORGANIZATION', :orgId, :canRead)
                        """)
                .param("teamId", teamId).param("orgId", orgId).param("canRead", canRead).update();
    }

    private void insertRolePolicy(String role, long orgId, boolean canRead) {
        jdbc.sql("""
                        INSERT INTO access_policy (subject_type, subject_role, scope_type, scope_organization_id, can_read)
                        VALUES ('ROLE', CAST(:role AS system_role), 'ORGANIZATION', :orgId, :canRead)
                        """)
                .param("role", role).param("orgId", orgId).param("canRead", canRead).update();
    }

    private void insertUserDocPolicy(long userId, long documentId, boolean canRead) {
        jdbc.sql("""
                        INSERT INTO access_policy (subject_type, subject_user_id, scope_type, scope_document_id, can_read)
                        VALUES ('USER', :userId, 'DOCUMENT', :documentId, :canRead)
                        """)
                .param("userId", userId).param("documentId", documentId).param("canRead", canRead).update();
    }
}

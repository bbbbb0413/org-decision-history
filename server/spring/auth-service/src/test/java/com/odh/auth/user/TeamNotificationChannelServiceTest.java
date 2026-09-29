package com.odh.auth.user;

import com.odh.auth.policy.PolicyService;
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
 * 팀별 알림 채널 설정의 관리 범위 판정과 upsert 동작.
 *
 * 픽스처는 {@link UserManagementServiceTest}와 같다: 플랫폼본부(org1)에 결제팀(team1)·
 * 인증팀(team2)·디자인팀(team4), 보안본부(org2)에 정보보안팀(team3).
 * admin=SYSTEM_ADMIN, lead=TEAM_ADMIN(team1 만 소속), orgAdmin=ORG_ADMIN(org1), member=MEMBER(org2, team3).
 */
@SpringBootTest
@Testcontainers
class TeamNotificationChannelServiceTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:16.4-alpine");

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private TeamNotificationChannelService service;

    @Autowired
    private JdbcClient jdbc;

    private long org1;
    private long org2;
    private long team1;
    private long team3;
    private long adminId;
    private long leadId;
    private long orgAdminId;
    private long memberId;

    @BeforeEach
    void seed() {
        jdbc.sql("TRUNCATE team_notification_channel, user_role, user_team, app_user, team, organization RESTART IDENTITY CASCADE").update();

        org1 = insertOrg("플랫폼본부");
        org2 = insertOrg("보안본부");
        team1 = insertTeam(org1, "결제팀");
        team3 = insertTeam(org2, "정보보안팀");

        adminId = insertUser("admin@t.com", org1);
        leadId = insertUser("lead@t.com", org1);
        orgAdminId = insertUser("orgadmin@t.com", org1);
        memberId = insertUser("member@t.com", org2);

        insertRole(adminId, "SYSTEM_ADMIN");
        insertRole(leadId, "TEAM_ADMIN");
        insertRole(orgAdminId, "ORG_ADMIN");
        insertRole(memberId, "MEMBER");

        jdbc.sql("INSERT INTO user_team (user_id, team_id) VALUES (:userId, :teamId)")
                .param("userId", leadId).param("teamId", team1).update();
    }

    @Test
    void unsetTeam_returnsEmptyRepresentation_notFoundError() {
        var result = service.get(adminId, team1);

        assertThat(result.slackWebhookUrl()).isNull();
        assertThat(result.teamsWebhookUrl()).isNull();
        assertThat(result.notificationEmails()).isEmpty();
        assertThat(result.updatedAt()).isNull();
    }

    @Test
    void systemAdmin_canConfigureAnyTeam() {
        var updated = service.update(adminId, team3,
                new TeamNotificationChannelDtos.UpdateRequest("https://hooks.slack.test/a", null, List.of("a@t.com", "b@t.com")));

        assertThat(updated.slackWebhookUrl()).isEqualTo("https://hooks.slack.test/a");
        assertThat(updated.notificationEmails()).containsExactly("a@t.com", "b@t.com");
        assertThat(updated.updatedAt()).isNotNull();
    }

    @Test
    void update_isUpsert_overwritesPreviousValue() {
        service.update(adminId, team1,
                new TeamNotificationChannelDtos.UpdateRequest("https://hooks.slack.test/old", null, null));

        var updated = service.update(adminId, team1,
                new TeamNotificationChannelDtos.UpdateRequest("https://hooks.slack.test/new", "https://outlook.test/hook", List.of("c@t.com")));

        assertThat(updated.slackWebhookUrl()).isEqualTo("https://hooks.slack.test/new");
        assertThat(updated.teamsWebhookUrl()).isEqualTo("https://outlook.test/hook");
        assertThat(updated.notificationEmails()).containsExactly("c@t.com");

        long rowCount = jdbc.sql("SELECT count(*) FROM team_notification_channel WHERE team_id = :id")
                .param("id", team1).query(Long.class).single();
        assertThat(rowCount).isEqualTo(1); // 새 행이 또 생기지 않고 덮어써야 한다
    }

    @Test
    void teamAdmin_canConfigureOwnTeam() {
        var updated = service.update(leadId, team1,
                new TeamNotificationChannelDtos.UpdateRequest(null, "https://outlook.test/hook", null));

        assertThat(updated.teamsWebhookUrl()).isEqualTo("https://outlook.test/hook");
    }

    @Test
    void teamAdmin_cannotConfigureTeamOutsideOwnScope() {
        assertThatThrownBy(() -> service.update(leadId, team3,
                new TeamNotificationChannelDtos.UpdateRequest("https://hooks.slack.test/x", null, null)))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    @Test
    void orgAdmin_cannotConfigureTeamInAnotherOrganization() {
        assertThatThrownBy(() -> service.update(orgAdminId, team3,
                new TeamNotificationChannelDtos.UpdateRequest("https://hooks.slack.test/x", null, null)))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    @Test
    void memberWithNoAdminRole_isRejected() {
        assertThatThrownBy(() -> service.get(memberId, team3))
                .isInstanceOf(PolicyService.ScopeViolationException.class);
    }

    @Test
    void unknownTeam_isNotFound() {
        assertThatThrownBy(() -> service.get(adminId, 9999L))
                .isInstanceOf(TeamNotificationChannelService.TeamNotFoundException.class);
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
                        VALUES (:email, 'x', 'x', :org, CAST('ACTIVE' AS user_status)) RETURNING id
                        """)
                .param("email", email).param("org", orgId)
                .query(Long.class).single();
    }

    private void insertRole(long userId, String role) {
        jdbc.sql("INSERT INTO user_role (user_id, role) VALUES (:userId, CAST(:role AS system_role))")
                .param("userId", userId).param("role", role).update();
    }
}

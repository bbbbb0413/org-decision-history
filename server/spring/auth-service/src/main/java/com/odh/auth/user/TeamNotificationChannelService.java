package com.odh.auth.user;

import com.odh.auth.domain.AppUser;
import com.odh.auth.domain.SystemRole;
import com.odh.auth.domain.Team;
import com.odh.auth.domain.UserRole;
import com.odh.auth.policy.PolicyService;
import com.odh.auth.repository.AppUserRepository;
import com.odh.auth.repository.TeamRepository;
import com.odh.auth.repository.UserRoleRepository;
import com.odh.auth.web.AuthService;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 팀별 거버넌스 알림 채널(Slack/Teams 웹훅, 알림 이메일) 관리.
 *
 * 조회·변경 권한은 {@link UserManagementService}의 관리 범위 판정과 같은 규칙을 쓴다:
 * SYSTEM_ADMIN 은 모든 팀, ORG_ADMIN 은 자기 조직 팀, TEAM_ADMIN 은 자기 소속 팀만.
 * 범위 밖 요청은 {@link PolicyService.ScopeViolationException}(403)으로 거부한다.
 */
@Service
@Transactional(readOnly = true)
public class TeamNotificationChannelService {

    private final TeamRepository teams;
    private final AppUserRepository users;
    private final UserRoleRepository roles;
    private final JdbcClient jdbc;

    public TeamNotificationChannelService(TeamRepository teams, AppUserRepository users,
                                          UserRoleRepository roles, JdbcClient jdbc) {
        this.teams = teams;
        this.users = users;
        this.roles = roles;
        this.jdbc = jdbc;
    }

    public TeamNotificationChannelDtos.TeamNotificationChannel get(Long actorId, Long teamId) {
        Team team = requireTeam(teamId);
        assertCanManage(actorId, team);
        return jdbc.sql("""
                        SELECT slack_webhook_url, teams_webhook_url, notification_email, updated_at
                        FROM team_notification_channel WHERE team_id = :teamId
                        """)
                .param("teamId", teamId)
                .query((rs, rowNum) -> toDto(teamId,
                        rs.getString("slack_webhook_url"),
                        rs.getString("teams_webhook_url"),
                        rs.getString("notification_email"),
                        rs.getTimestamp("updated_at") == null ? null : rs.getTimestamp("updated_at").toInstant()))
                .optional()
                .orElseGet(() -> toDto(teamId, null, null, null, null));
    }

    @Transactional
    public TeamNotificationChannelDtos.TeamNotificationChannel update(
            Long actorId, Long teamId, TeamNotificationChannelDtos.UpdateRequest request) {
        Team team = requireTeam(teamId);
        assertCanManage(actorId, team);

        String emails = request.notificationEmails() == null || request.notificationEmails().isEmpty()
                ? null
                : String.join(",", request.notificationEmails());

        jdbc.sql("""
                        INSERT INTO team_notification_channel
                            (team_id, slack_webhook_url, teams_webhook_url, notification_email, updated_at)
                        VALUES (:teamId, :slack, :teamsWebhook, :emails, now())
                        ON CONFLICT (team_id) DO UPDATE SET
                            slack_webhook_url = EXCLUDED.slack_webhook_url,
                            teams_webhook_url = EXCLUDED.teams_webhook_url,
                            notification_email = EXCLUDED.notification_email,
                            updated_at = now()
                        """)
                .param("teamId", teamId)
                .param("slack", request.slackWebhookUrl())
                .param("teamsWebhook", request.teamsWebhookUrl())
                .param("emails", emails)
                .update();

        return get(actorId, teamId);
    }

    private TeamNotificationChannelDtos.TeamNotificationChannel toDto(
            Long teamId, String slack, String teamsWebhook, String emails, Instant updatedAt) {
        List<String> emailList = emails == null || emails.isBlank()
                ? List.of()
                : Arrays.stream(emails.split(",")).map(String::strip).filter(s -> !s.isBlank()).toList();
        return new TeamNotificationChannelDtos.TeamNotificationChannel(teamId, slack, teamsWebhook, emailList, updatedAt);
    }

    private Team requireTeam(Long teamId) {
        return teams.findById(teamId).orElseThrow(TeamNotFoundException::new);
    }

    private void assertCanManage(Long actorId, Team team) {
        AppUser actor = users.findById(actorId).orElseThrow(AuthService.UserNotFoundException::new);
        Set<SystemRole> actorRoles = roles.findByUserId(actorId).stream()
                .map(UserRole::getRole).collect(Collectors.toSet());

        if (actorRoles.contains(SystemRole.SYSTEM_ADMIN)) {
            return;
        }
        if (actorRoles.contains(SystemRole.ORG_ADMIN)
                && team.getOrganizationId().equals(actor.getOrganizationId())) {
            return;
        }
        if (actorRoles.contains(SystemRole.TEAM_ADMIN)) {
            boolean memberOfTeam = teams.findByUserId(actorId).stream()
                    .anyMatch(t -> t.getId().equals(team.getId()));
            if (memberOfTeam) {
                return;
            }
        }
        throw new PolicyService.ScopeViolationException();
    }

    public static class TeamNotFoundException extends RuntimeException {
    }
}

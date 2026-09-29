package com.odh.auth.policy;

import com.odh.common.PermissionScope;
import com.odh.auth.domain.SystemRole;
import com.odh.auth.repository.UserRoleRepository;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashSet;
import java.util.Set;

/**
 * 사용자의 읽기 범위를 계산한다.
 *
 * 세 경로에서 권한이 들어온다.
 *   1. 사용자에게 직접 준 정책
 *   2. 사용자가 속한 팀에 준 정책
 *   3. 사용자가 가진 역할에 준 정책
 * 세 결과를 합치고, can_read = false 인 차단 정책을 빼서 최종 범위를 만든다.
 * 차단이 허용보다 우선하는 이유는 예외를 두려면 명시적으로 막을 수 있어야 하기 때문이다.
 */
@Service
@Transactional(readOnly = true)
public class PermissionScopeService {

    private static final String ALLOW_SQL = """
            SELECT p.scope_type::text AS scope_type,
                   COALESCE(p.scope_organization_id, p.scope_team_id, p.scope_document_id) AS scope_id
            FROM access_policy p
            WHERE p.revoked_at IS NULL
              AND p.can_read = :canRead
              AND (
                    (p.subject_type = 'USER' AND p.subject_user_id = :userId)
                 OR (p.subject_type = 'TEAM' AND p.subject_team_id IN
                        (SELECT ut.team_id FROM user_team ut WHERE ut.user_id = :userId))
                 OR (p.subject_type = 'ROLE' AND p.subject_role::text IN
                        (SELECT ur.role::text FROM user_role ur WHERE ur.user_id = :userId))
              )
            """;

    private final JdbcClient jdbc;
    private final UserRoleRepository roles;

    public PermissionScopeService(JdbcClient jdbc, UserRoleRepository roles) {
        this.jdbc = jdbc;
        this.roles = roles;
    }

    public PermissionScope calculate(Long userId) {
        boolean systemAdmin = roles.findByUserId(userId).stream()
                .anyMatch(r -> r.getRole() == SystemRole.SYSTEM_ADMIN);

        Set<Long> orgs = new LinkedHashSet<>();
        Set<Long> teams = new LinkedHashSet<>();
        Set<Long> docs = new LinkedHashSet<>();
        collect(userId, true, orgs, teams, docs);

        Set<Long> deniedOrgs = new LinkedHashSet<>();
        Set<Long> deniedTeams = new LinkedHashSet<>();
        Set<Long> deniedDocs = new LinkedHashSet<>();
        collect(userId, false, deniedOrgs, deniedTeams, deniedDocs);

        // 차단이 허용을 이긴다
        orgs.removeAll(deniedOrgs);
        teams.removeAll(deniedTeams);
        docs.removeAll(deniedDocs);

        return new PermissionScope(
                Set.copyOf(orgs), Set.copyOf(teams), Set.copyOf(docs),
                Set.copyOf(deniedDocs), systemAdmin);
    }

    private void collect(Long userId, boolean canRead,
                         Set<Long> orgs, Set<Long> teams, Set<Long> docs) {
        jdbc.sql(ALLOW_SQL)
                .param("userId", userId)
                .param("canRead", canRead)
                .query((rs, i) -> {
                    String type = rs.getString("scope_type");
                    long id = rs.getLong("scope_id");
                    switch (type) {
                        case "ORGANIZATION" -> orgs.add(id);
                        case "TEAM" -> teams.add(id);
                        case "DOCUMENT" -> docs.add(id);
                        default -> { }
                    }
                    return null;
                })
                .list();
    }
}

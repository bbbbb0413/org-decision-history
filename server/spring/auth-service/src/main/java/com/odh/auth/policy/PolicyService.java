package com.odh.auth.policy;

import com.odh.auth.domain.SystemRole;
import com.odh.auth.repository.UserRoleRepository;
import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/** 접근 정책 등록·조회·회수. */
@Service
@Transactional(readOnly = true)
public class PolicyService {

    private static final String SELECT_BASE = """
            SELECT p.id, p.subject_type::text AS subject_type, p.subject_user_id, p.subject_team_id,
                   p.subject_role::text AS subject_role,
                   COALESCE(su.name, st.name, p.subject_role::text) AS subject_name,
                   p.scope_type::text AS scope_type, p.scope_organization_id, p.scope_team_id,
                   p.scope_document_id,
                   COALESCE(so.name, st2.name, '문서 #' || p.scope_document_id) AS scope_name,
                   p.can_read, p.created_by, p.created_at, p.revoked_at
            FROM access_policy p
            LEFT JOIN app_user     su  ON su.id  = p.subject_user_id
            LEFT JOIN team         st  ON st.id  = p.subject_team_id
            LEFT JOIN organization so  ON so.id  = p.scope_organization_id
            LEFT JOIN team         st2 ON st2.id = p.scope_team_id
            WHERE (:includeRevoked OR p.revoked_at IS NULL)
            ORDER BY p.created_at DESC
            LIMIT :limit OFFSET :offset
            """;

    private final JdbcClient jdbc;
    private final UserRoleRepository roles;
    private final AuditService audit;

    public PolicyService(JdbcClient jdbc, UserRoleRepository roles, AuditService audit) {
        this.jdbc = jdbc;
        this.roles = roles;
        this.audit = audit;
    }

    /**
     * 접근 정책 목록. 관리자만 볼 수 있다.
     *
     * <p>막지 않으면 MEMBER 가 조직 전체의 권한 구조를 그대로 읽을 수 있다.
     * 쓰기(create·revoke)와 같은 기준(MEMBER 가 아닐 것)을 적용한다 — 프론트의
     * "접근 관리" 탭도 같은 기준으로 노출된다(HomePage 의 isAdmin).
     */
    public PageResponse<PolicyDtos.Policy> list(Long actorId, boolean includeRevoked, int page, int size) {
        requireAdmin(actorId, "POLICY_VIEW", "정책 조회 권한 없음");
        PageRequest paging = PageRequest.of(page, size);
        List<PolicyDtos.Policy> content = jdbc.sql(SELECT_BASE)
                .param("includeRevoked", includeRevoked)
                .param("limit", paging.size())
                .param("offset", paging.offset())
                .query((rs, i) -> new PolicyDtos.Policy(
                        rs.getLong("id"),
                        PolicyDtos.SubjectType.valueOf(rs.getString("subject_type")),
                        (Long) rs.getObject("subject_user_id"),
                        (Long) rs.getObject("subject_team_id"),
                        rs.getString("subject_role") == null ? null : SystemRole.valueOf(rs.getString("subject_role")),
                        rs.getString("subject_name"),
                        PolicyDtos.ScopeType.valueOf(rs.getString("scope_type")),
                        (Long) rs.getObject("scope_organization_id"),
                        (Long) rs.getObject("scope_team_id"),
                        (Long) rs.getObject("scope_document_id"),
                        rs.getString("scope_name"),
                        rs.getBoolean("can_read"),
                        (Long) rs.getObject("created_by"),
                        rs.getTimestamp("created_at").toInstant(),
                        rs.getTimestamp("revoked_at") == null ? null : rs.getTimestamp("revoked_at").toInstant()))
                .list();

        long total = jdbc.sql("SELECT count(*) FROM access_policy WHERE (:includeRevoked OR revoked_at IS NULL)")
                .param("includeRevoked", includeRevoked)
                .query(Long.class).single();

        return PageResponse.of(content, paging.page(), paging.size(), total);
    }

    @Transactional
    public Long create(Long actorId, PolicyDtos.PolicyRequest request) {
        requireAdmin(actorId, "POLICY_CHANGE", "관리 권한 없음");

        Long id = jdbc.sql("""
                        INSERT INTO access_policy
                            (subject_type, subject_user_id, subject_team_id, subject_role,
                             scope_type, scope_organization_id, scope_team_id, scope_document_id,
                             can_read, created_by)
                        VALUES (CAST(:subjectType AS policy_subject_type), :subjectUserId, :subjectTeamId,
                                CAST(:subjectRole AS system_role),
                                CAST(:scopeType AS policy_scope_type), :scopeOrganizationId, :scopeTeamId,
                                :scopeDocumentId, :canRead, :createdBy)
                        RETURNING id
                        """)
                .param("subjectType", request.subjectType().name())
                .param("subjectUserId", request.subjectUserId())
                .param("subjectTeamId", request.subjectTeamId())
                .param("subjectRole", request.subjectRole() == null ? null : request.subjectRole().name())
                .param("scopeType", request.scopeType().name())
                .param("scopeOrganizationId", request.scopeOrganizationId())
                .param("scopeTeamId", request.scopeTeamId())
                .param("scopeDocumentId", request.scopeDocumentId())
                .param("canRead", request.canReadOrDefault())
                .param("createdBy", actorId)
                .query(Long.class).single();

        audit.record(actorId, "POLICY_CHANGE", "POLICY", id, true,
                request.canReadOrDefault() ? "정책 등록 (허용)" : "정책 등록 (차단)");
        return id;
    }

    @Transactional
    public boolean revoke(Long actorId, Long policyId) {
        requireAdmin(actorId, "POLICY_CHANGE", "관리 권한 없음");
        int updated = jdbc.sql("UPDATE access_policy SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL")
                .param("id", policyId).update();
        audit.record(actorId, "POLICY_CHANGE", "POLICY", policyId, updated > 0, "정책 회수");
        return updated > 0;
    }

    /**
     * 감사 로그. 관리자만 볼 수 있다.
     *
     * <p>감사 로그는 내부 감시 수단이라 피감시자가 읽을 수 있으면 목적이 무너진다.
     * 막지 않으면 MEMBER 가 전 사용자의 실명·행위·거부 사유를 읽을 수 있다.
     */
    public PageResponse<PolicyDtos.AuditEntry> auditLogs(Long actorId, int page, int size) {
        requireAdmin(actorId, "AUDIT_VIEW", "감사 로그 조회 권한 없음");
        PageRequest paging = PageRequest.of(page, size);
        List<PolicyDtos.AuditEntry> content = jdbc.sql("""
                        SELECT a.id, a.actor_id, u.name AS actor_name, a.action::text AS action,
                               a.target_type, a.target_id, a.result::text AS result, a.reason,
                               a.correlation_id, a.occurred_at
                        FROM audit_log a
                        LEFT JOIN app_user u ON u.id = a.actor_id
                        ORDER BY a.occurred_at DESC
                        LIMIT :limit OFFSET :offset
                        """)
                .param("limit", paging.size()).param("offset", paging.offset())
                .query((rs, i) -> new PolicyDtos.AuditEntry(
                        rs.getLong("id"), (Long) rs.getObject("actor_id"), rs.getString("actor_name"),
                        rs.getString("action"), rs.getString("target_type"), (Long) rs.getObject("target_id"),
                        rs.getString("result"), rs.getString("reason"), rs.getString("correlation_id"),
                        rs.getTimestamp("occurred_at").toInstant()))
                .list();

        long total = jdbc.sql("SELECT count(*) FROM audit_log").query(Long.class).single();
        return PageResponse.of(content, paging.page(), paging.size(), total);
    }

    /**
     * 조직·팀 관리자는 자기 범위만, 시스템 관리자는 전체를 관리한다.
     *
     * <p>거부만 감사에 남긴다. 성공까지 남기면 감사 로그를 한 번 볼 때마다 감사 로그가
     * 늘어나 목록이 자기 자신으로 찬다. action 을 인자로 받는 이유는 조회 거부를
     * POLICY_CHANGE 로 적으면 "정책을 바꾸려다 막혔다"로 읽혀 사실과 다르기 때문이다.
     */
    private void requireAdmin(Long actorId, String action, String denyReason) {
        boolean admin = roles.findByUserId(actorId).stream()
                .anyMatch(r -> r.getRole() != SystemRole.MEMBER);
        if (!admin) {
            audit.record(actorId, action, "POLICY", null, false, denyReason);
            throw new ScopeViolationException();
        }
    }

    public static class ScopeViolationException extends RuntimeException {
    }
}

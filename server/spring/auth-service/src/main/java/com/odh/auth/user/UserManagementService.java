package com.odh.auth.user;

import com.odh.auth.domain.AppUser;
import com.odh.auth.domain.Organization;
import com.odh.auth.domain.SystemRole;
import com.odh.auth.domain.Team;
import com.odh.auth.domain.UserRole;
import com.odh.auth.domain.UserStatus;
import com.odh.auth.jwt.TokenRevoker;
import com.odh.auth.policy.AuditService;
import com.odh.auth.policy.PolicyService;
import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import com.odh.auth.repository.AppUserRepository;
import com.odh.auth.repository.OrganizationRepository;
import com.odh.auth.repository.TeamRepository;
import com.odh.auth.repository.UserRoleRepository;
import com.odh.auth.web.AuthService;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * 사용자 조직·팀 소속과 시스템 역할 관리.
 *
 * 관리자의 종류에 따라 관리 범위가 다르다.
 *   - SYSTEM_ADMIN: 전체 사용자, 어떤 역할이든 부여한다.
 *   - ORG_ADMIN: 자기 조직 사용자만, SYSTEM_ADMIN 은 부여할 수 없다.
 *   - TEAM_ADMIN: 자기 소속 팀과 겹치는 사용자만, TEAM_ADMIN·MEMBER 만 부여한다.
 * 범위 밖 요청은 조회·변경 모두 거부한다. 등록·변경 이력은 auth_audit 로그의
 * ROLE_CHANGE 로 남긴다 (소속과 역할을 하나의 감사 사유로 묶는다).
 */
@Service
@Transactional(readOnly = true)
public class UserManagementService {

    private enum ScopeKind { SYSTEM, ORG, TEAM }

    /** 관리자의 관리 범위. TEAM 범위는 소속 팀 집합으로, ORG 범위는 조직 하나로 표현한다. */
    private record Scope(ScopeKind kind, Long organizationId, Set<Long> teamIds) {
    }

    private final AppUserRepository users;
    private final OrganizationRepository organizations;
    private final TeamRepository teams;
    private final UserRoleRepository roles;
    private final AuditService audit;
    private final TokenRevoker tokenRevoker;
    private final JdbcClient jdbc;
    private final BCryptPasswordEncoder passwordEncoder = new BCryptPasswordEncoder();
    /** 혼동하기 쉬운 문자(0/O, 1/l/I)를 뺀 임시 비밀번호용 문자 집합. */
    private static final String TEMP_PASSWORD_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$%";
    private static final int TEMP_PASSWORD_LENGTH = 12;
    private final SecureRandom random = new SecureRandom();

    public UserManagementService(AppUserRepository users, OrganizationRepository organizations,
                                 TeamRepository teams, UserRoleRepository roles,
                                 AuditService audit, TokenRevoker tokenRevoker, JdbcClient jdbc) {
        this.users = users;
        this.organizations = organizations;
        this.teams = teams;
        this.roles = roles;
        this.audit = audit;
        this.tokenRevoker = tokenRevoker;
        this.jdbc = jdbc;
    }

    public PageResponse<UserDtos.UserSummary> list(Long actorId, String q, int page, int size) {
        PageRequest paging = PageRequest.of(page, size);
        Scope scope = scopeOf(actorId);
        if (scope.kind() == ScopeKind.TEAM && scope.teamIds().isEmpty()) {
            return PageResponse.of(List.of(), paging.page(), paging.size(), 0);
        }

        String scopeFilter = switch (scope.kind()) {
            case SYSTEM -> "";
            case ORG -> "AND u.organization_id = :scopeOrgId";
            case TEAM -> "AND u.id IN (SELECT ut.user_id FROM user_team ut WHERE ut.team_id IN (:scopeTeamIds))";
        };

        // 문자열 연결로 조각을 끼워 넣는다. String.formatted 를 쓰면 LIKE 패턴의 '%' 를
        // 서식 지정자로 오인해 예외가 난다.
        List<Long> ids = jdbc.sql("""
                        SELECT u.id
                        FROM app_user u
                        WHERE (CAST(:q AS varchar) IS NULL OR u.name ILIKE '%' || CAST(:q AS varchar) || '%' OR u.email ILIKE '%' || CAST(:q AS varchar) || '%')
                        """ + scopeFilter + """

                        ORDER BY u.name
                        LIMIT :limit OFFSET :offset
                        """)
                .param("q", q)
                .param("scopeOrgId", scope.organizationId())
                .param("scopeTeamIds", scope.teamIds())
                .param("limit", paging.size())
                .param("offset", paging.offset())
                .query(Long.class)
                .list();

        long total = jdbc.sql("""
                        SELECT count(*) FROM app_user u
                        WHERE (CAST(:q AS varchar) IS NULL OR u.name ILIKE '%' || CAST(:q AS varchar) || '%' OR u.email ILIKE '%' || CAST(:q AS varchar) || '%')
                        """ + scopeFilter)
                .param("q", q)
                .param("scopeOrgId", scope.organizationId())
                .param("scopeTeamIds", scope.teamIds())
                .query(Long.class).single();

        List<UserDtos.UserSummary> content = ids.isEmpty() ? List.of() : summariesOf(ids);
        return PageResponse.of(content, paging.page(), paging.size(), total);
    }

    public UserDtos.UserDetail get(Long actorId, Long targetId) {
        AppUser target = users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new);
        Scope scope = scopeOf(actorId);
        if (!inScope(scope, target)) {
            throw new PolicyService.ScopeViolationException();
        }
        return detailOf(target);
    }

    @Transactional
    public UserDtos.UserDetail updateMembership(Long actorId, Long targetId, UserDtos.MembershipRequest request) {
        AppUser target = users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new);
        Scope scope = scopeOf(actorId);
        if (!inScope(scope, target)) {
            throw new PolicyService.ScopeViolationException();
        }
        if (!organizations.existsById(request.organizationId())) {
            throw new ValidationException("organizationId", "존재하지 않는 조직입니다.");
        }

        // ORG_ADMIN 은 자기 조직 밖으로 옮길 수 없고, TEAM_ADMIN 은 조직 자체를 바꿀 수 없다.
        if (scope.kind() == ScopeKind.ORG && !Objects.equals(request.organizationId(), scope.organizationId())) {
            throw new PolicyService.ScopeViolationException();
        }
        if (scope.kind() == ScopeKind.TEAM && !Objects.equals(request.organizationId(), target.getOrganizationId())) {
            throw new PolicyService.ScopeViolationException();
        }

        List<Team> requestedTeams = teams.findAllById(request.teamIds());
        if (requestedTeams.size() != Set.copyOf(request.teamIds()).size()) {
            throw new ValidationException("teamIds", "존재하지 않는 팀이 포함되어 있습니다.");
        }
        boolean allInOrg = requestedTeams.stream()
                .allMatch(t -> Objects.equals(t.getOrganizationId(), request.organizationId()));
        if (!allInOrg) {
            throw new ValidationException("teamIds", "선택한 팀이 대상 조직에 속하지 않습니다.");
        }

        Set<Long> finalTeamIds = resolveFinalTeamIds(scope, targetId, request.teamIds());

        if (!Objects.equals(request.organizationId(), target.getOrganizationId())) {
            users.updateOrganization(targetId, request.organizationId());
        }
        jdbc.sql("DELETE FROM user_team WHERE user_id = :id").param("id", targetId).update();
        for (Long teamId : finalTeamIds) {
            jdbc.sql("INSERT INTO user_team (user_id, team_id, assigned_by) VALUES (:userId, :teamId, :actorId)")
                    .param("userId", targetId).param("teamId", teamId).param("actorId", actorId)
                    .update();
        }

        audit.record(actorId, "ROLE_CHANGE", "USER", targetId, true, "소속 변경");

        // updateOrganization 이 clearAutomatically 로 영속성 컨텍스트를 비우므로,
        // target 이 이 메서드 시작에서 이미 조회돼 있었더라도 아래 재조회는 캐시가 아니라
        // DB 를 다시 읽어 방금 바꾼 organization_id 를 그대로 돌려준다.
        AppUser refreshed = users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new);
        String orgName = organizations.findById(refreshed.getOrganizationId()).map(Organization::getName).orElse(null);
        List<UserDtos.TeamRef> teamRefs = teams.findByUserId(targetId).stream()
                .map(t -> new UserDtos.TeamRef(t.getId(), t.getOrganizationId(), t.getName())).toList();
        List<SystemRole> userRoles = roles.findByUserId(targetId).stream().map(UserRole::getRole).toList();
        return new UserDtos.UserDetail(
                refreshed.getId(), refreshed.getEmail(), refreshed.getName(), refreshed.getStatus().name(),
                refreshed.getOrganizationId(), orgName, teamRefs, userRoles,
                refreshed.getCreatedAt(), refreshed.getCreatedAt());
    }

    /**
     * TEAM_ADMIN 의 요청은 자기 팀 범위 안에서만 반영한다. 전체 교체 방식 요청을 그대로 적용하면
     * 관리 범위 밖 팀 소속까지 해제될 수 있어, 범위 밖 소속은 기존 값을 그대로 둔다.
     */
    private Set<Long> resolveFinalTeamIds(Scope scope, Long targetId, List<Long> requestedTeamIds) {
        if (scope.kind() != ScopeKind.TEAM) {
            return Set.copyOf(requestedTeamIds);
        }
        Set<Long> currentTeamIds = teams.findByUserId(targetId).stream().map(Team::getId)
                .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new));
        Set<Long> outsideScope = new LinkedHashSet<>(currentTeamIds);
        outsideScope.removeAll(scope.teamIds());

        Set<Long> withinScopeRequested = new LinkedHashSet<>(requestedTeamIds);
        withinScopeRequested.retainAll(scope.teamIds());

        Set<Long> result = new LinkedHashSet<>(outsideScope);
        result.addAll(withinScopeRequested);
        return result;
    }

    @Transactional
    public UserDtos.UserDetail updateRoles(Long actorId, Long targetId, UserDtos.RolesRequest request) {
        AppUser target = users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new);
        Scope scope = scopeOf(actorId);
        if (!inScope(scope, target)) {
            throw new PolicyService.ScopeViolationException();
        }

        Set<SystemRole> ceiling = ceilingOf(scope);
        if (!ceiling.containsAll(request.roles())) {
            throw new PolicyService.ScopeViolationException();
        }
        // 대상이 이미 자기 상한을 넘는 역할(예: SYSTEM_ADMIN)을 갖고 있으면 통째로 거부한다.
        // 그렇지 않으면 "전체 교체" 방식 특성상 팀 관리자가 같은 팀의 시스템 관리자 역할을
        // 지워버릴 수 있다 — 요청 역할만 검사해서는 이 강등을 막지 못한다.
        assertTargetWithinCeiling(scope, targetId);

        jdbc.sql("DELETE FROM user_role WHERE user_id = :id").param("id", targetId).update();
        for (SystemRole role : Set.copyOf(request.roles())) {
            jdbc.sql("INSERT INTO user_role (user_id, role, granted_by) VALUES (:userId, CAST(:role AS system_role), :actorId)")
                    .param("userId", targetId).param("role", role.name()).param("actorId", actorId)
                    .update();
        }

        audit.record(actorId, "ROLE_CHANGE", "USER", targetId, true,
                "역할 변경: " + request.roles().stream().map(Enum::name).collect(java.util.stream.Collectors.joining(", ")));
        return detailOf(users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new));
    }

    /** 관리자가 부여·회수할 수 있는 역할의 상한. 자기보다 높은 권한은 못 만든다. */
    private static Set<SystemRole> ceilingOf(Scope scope) {
        return switch (scope.kind()) {
            case SYSTEM -> Set.of(SystemRole.SYSTEM_ADMIN, SystemRole.ORG_ADMIN, SystemRole.TEAM_ADMIN, SystemRole.MEMBER);
            case ORG -> Set.of(SystemRole.ORG_ADMIN, SystemRole.TEAM_ADMIN, SystemRole.MEMBER);
            case TEAM -> Set.of(SystemRole.TEAM_ADMIN, SystemRole.MEMBER);
        };
    }

    /**
     * 대상이 이미 관리자의 상한을 넘는 역할을 갖고 있으면 거부한다.
     * 역할 변경뿐 아니라 비활성화·비밀번호 재발급에도 같은 규칙이 필요하다 — 안 그러면
     * 팀 관리자가 역할은 못 건드려도 계정을 잠그거나 비밀번호를 갈아치워 같은 효과를 낼 수 있다.
     */
    private void assertTargetWithinCeiling(Scope scope, Long targetId) {
        List<SystemRole> currentRoles = roles.findByUserId(targetId).stream().map(UserRole::getRole).toList();
        if (!ceilingOf(scope).containsAll(currentRoles)) {
            throw new PolicyService.ScopeViolationException();
        }
    }

    /**
     * 계정 활성/비활성 전환.
     *
     * 비활성화하면 토큰도 함께 무효화한다. 로그인만 막으면 이미 발급된 액세스 토큰이
     * 만료될 때까지(기본 30분) 퇴사자가 그대로 쓸 수 있다.
     */
    @Transactional
    public UserDtos.UserDetail updateStatus(Long actorId, Long targetId, UserDtos.StatusRequest request) {
        // 자기 계정을 잠그면 스스로도 되돌릴 수 없다. 다른 관리자를 거치게 한다.
        if (Objects.equals(actorId, targetId)) {
            throw new ValidationException("status", "자기 계정의 상태는 바꿀 수 없습니다.");
        }
        AppUser target = users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new);
        Scope scope = scopeOf(actorId);
        if (!inScope(scope, target)) {
            throw new PolicyService.ScopeViolationException();
        }
        assertTargetWithinCeiling(scope, targetId);

        users.updateStatus(targetId, request.status());
        if (request.status() == UserStatus.DISABLED) {
            tokenRevoker.revokeAll(targetId);
        }

        audit.record(actorId, "USER_STATUS_CHANGE", "USER", targetId, true,
                "계정 상태 변경: " + request.status().name());
        return detailOf(users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new));
    }

    /**
     * 관리자에 의한 임시 비밀번호 재발급.
     *
     * 비밀번호를 잊은 사용자를 위한 경로다. 계정 발급과 마찬가지로 평문은 이 응답에서만
     * 보여주고 저장은 해시로만 한다. 재발급과 동시에 기존 토큰을 끊는다 — 비밀번호를
     * 새로 받았다는 건 예전 비밀번호를 믿을 수 없다는 뜻이다.
     */
    @Transactional
    public UserDtos.IssuedTemporaryPassword resetPassword(Long actorId, Long targetId) {
        AppUser target = users.findById(targetId).orElseThrow(AuthService.UserNotFoundException::new);
        Scope scope = scopeOf(actorId);
        if (!inScope(scope, target)) {
            throw new PolicyService.ScopeViolationException();
        }
        assertTargetWithinCeiling(scope, targetId);

        String temporaryPassword = generateTemporaryPassword();
        users.updatePasswordHash(targetId, passwordEncoder.encode(temporaryPassword));
        tokenRevoker.revokeAll(targetId);

        audit.record(actorId, "PASSWORD_RESET", "USER", targetId, true, "임시 비밀번호 재발급: " + target.getEmail());
        return new UserDtos.IssuedTemporaryPassword(targetId, target.getEmail(), temporaryPassword);
    }

    public List<UserDtos.OrganizationRef> listOrganizations(Long actorId) {
        Scope scope = scopeOf(actorId);
        List<Organization> visible = scope.kind() == ScopeKind.SYSTEM
                ? organizations.findAll()
                : organizations.findById(scope.organizationId()).map(List::of).orElse(List.of());
        return visible.stream().map(o -> new UserDtos.OrganizationRef(o.getId(), o.getName())).toList();
    }

    public List<UserDtos.TeamRef> listTeams(Long actorId, Long organizationId) {
        Scope scope = scopeOf(actorId);
        boolean allowed = scope.kind() == ScopeKind.SYSTEM || Objects.equals(scope.organizationId(), organizationId);
        if (!allowed) {
            throw new PolicyService.ScopeViolationException();
        }
        return teams.findByOrganizationIdOrderByName(organizationId).stream()
                .map(t -> new UserDtos.TeamRef(t.getId(), t.getOrganizationId(), t.getName()))
                .toList();
    }

    /** 조직 생성. B2B 온보딩의 최소 요건이라 시스템 관리자만 할 수 있다. */
    @Transactional
    public UserDtos.OrganizationRef createOrganization(Long actorId, UserDtos.CreateOrganizationRequest request) {
        Scope scope = scopeOf(actorId);
        if (scope.kind() != ScopeKind.SYSTEM) {
            throw new PolicyService.ScopeViolationException();
        }
        if (organizations.existsByNameIgnoreCase(request.name())) {
            throw new ValidationException("name", "이미 존재하는 조직명입니다.");
        }

        Long id = jdbc.sql("INSERT INTO organization (name) VALUES (:name) RETURNING id")
                .param("name", request.name())
                .query(Long.class)
                .single();

        audit.record(actorId, "ORGANIZATION_CREATED", "ORGANIZATION", id, true, "조직 생성: " + request.name());
        return new UserDtos.OrganizationRef(id, request.name());
    }

    /**
     * 사용자 계정 발급.
     *
     * 서버가 임시 비밀번호를 생성해 이 응답에서만 평문으로 돌려주고, 저장은 해시로만 한다.
     * 관리 범위·역할 상한 판정은 updateMembership/updateRoles 와 같은 규칙을 그대로 쓴다 —
     * "새로 만들기"와 "바꾸기"가 서로 다른 권한 모델을 쓰면 둘 중 하나로 우회할 수 있다.
     */
    @Transactional
    public UserDtos.IssuedUserAccount createUserAccount(Long actorId, UserDtos.CreateUserAccountRequest request) {
        Scope scope = scopeOf(actorId);

        if (users.findByEmail(request.email()).isPresent()) {
            throw new ValidationException("email", "이미 사용 중인 이메일입니다.");
        }
        Organization org = organizations.findById(request.organizationId())
                .orElseThrow(() -> new ValidationException("organizationId", "존재하지 않는 조직입니다."));

        if ((scope.kind() == ScopeKind.ORG || scope.kind() == ScopeKind.TEAM)
                && !Objects.equals(request.organizationId(), scope.organizationId())) {
            throw new PolicyService.ScopeViolationException();
        }

        List<Long> teamIds = new ArrayList<>(new LinkedHashSet<>(
                request.teamIds() == null ? List.of() : request.teamIds()));
        List<Team> requestedTeams = teams.findAllById(teamIds);
        if (requestedTeams.size() != teamIds.size()) {
            throw new ValidationException("teamIds", "존재하지 않는 팀이 포함되어 있습니다.");
        }
        boolean allInOrg = requestedTeams.stream()
                .allMatch(t -> Objects.equals(t.getOrganizationId(), request.organizationId()));
        if (!allInOrg) {
            throw new ValidationException("teamIds", "선택한 팀이 대상 조직에 속하지 않습니다.");
        }
        if (scope.kind() == ScopeKind.TEAM && !scope.teamIds().containsAll(teamIds)) {
            throw new PolicyService.ScopeViolationException();
        }

        if (!ceilingOf(scope).containsAll(request.roles())) {
            throw new PolicyService.ScopeViolationException();
        }
        List<SystemRole> distinctRoles = new ArrayList<>(new LinkedHashSet<>(request.roles()));

        String temporaryPassword = generateTemporaryPassword();
        Long userId = jdbc.sql("""
                        INSERT INTO app_user (email, password_hash, name, organization_id, status)
                        VALUES (:email, :passwordHash, :name, :organizationId, 'ACTIVE')
                        RETURNING id
                        """)
                .param("email", request.email())
                .param("passwordHash", passwordEncoder.encode(temporaryPassword))
                .param("name", request.name())
                .param("organizationId", request.organizationId())
                .query(Long.class)
                .single();

        for (Long teamId : teamIds) {
            jdbc.sql("INSERT INTO user_team (user_id, team_id, assigned_by) VALUES (:userId, :teamId, :actorId)")
                    .param("userId", userId).param("teamId", teamId).param("actorId", actorId)
                    .update();
        }
        for (SystemRole role : distinctRoles) {
            jdbc.sql("INSERT INTO user_role (user_id, role, granted_by) VALUES (:userId, CAST(:role AS system_role), :actorId)")
                    .param("userId", userId).param("role", role.name()).param("actorId", actorId)
                    .update();
        }

        audit.record(actorId, "USER_CREATED", "USER", userId, true, "계정 발급: " + request.email());

        AppUser created = users.findById(userId).orElseThrow(AuthService.UserNotFoundException::new);
        List<UserDtos.TeamRef> teamRefs = teams.findByUserId(userId).stream()
                .map(t -> new UserDtos.TeamRef(t.getId(), t.getOrganizationId(), t.getName())).toList();
        return new UserDtos.IssuedUserAccount(
                created.getId(), created.getEmail(), created.getName(), created.getStatus().name(),
                request.organizationId(), org.getName(), teamRefs, distinctRoles,
                created.getCreatedAt(), created.getCreatedAt(), temporaryPassword);
    }

    private String generateTemporaryPassword() {
        StringBuilder sb = new StringBuilder(TEMP_PASSWORD_LENGTH);
        for (int i = 0; i < TEMP_PASSWORD_LENGTH; i++) {
            sb.append(TEMP_PASSWORD_CHARS.charAt(random.nextInt(TEMP_PASSWORD_CHARS.length())));
        }
        return sb.toString();
    }

    private Scope scopeOf(Long actorId) {
        AppUser actor = users.findById(actorId).orElseThrow(AuthService.UserNotFoundException::new);
        Set<SystemRole> actorRoles = roles.findByUserId(actorId).stream()
                .map(UserRole::getRole).collect(java.util.stream.Collectors.toSet());

        if (actorRoles.contains(SystemRole.SYSTEM_ADMIN)) {
            return new Scope(ScopeKind.SYSTEM, null, Set.of());
        }
        if (actorRoles.contains(SystemRole.ORG_ADMIN)) {
            return new Scope(ScopeKind.ORG, actor.getOrganizationId(), Set.of());
        }
        if (actorRoles.contains(SystemRole.TEAM_ADMIN)) {
            Set<Long> teamIds = teams.findByUserId(actorId).stream().map(Team::getId)
                    .collect(java.util.stream.Collectors.toCollection(LinkedHashSet::new));
            return new Scope(ScopeKind.TEAM, actor.getOrganizationId(), teamIds);
        }
        throw new PolicyService.ScopeViolationException();
    }

    private boolean inScope(Scope scope, AppUser target) {
        return switch (scope.kind()) {
            case SYSTEM -> true;
            case ORG -> Objects.equals(target.getOrganizationId(), scope.organizationId());
            case TEAM -> !Collections.disjoint(
                    teams.findByUserId(target.getId()).stream().map(Team::getId).toList(),
                    scope.teamIds());
        };
    }

    private UserDtos.UserDetail detailOf(AppUser user) {
        String orgName = user.getOrganizationId() == null ? null
                : organizations.findById(user.getOrganizationId()).map(Organization::getName).orElse(null);
        List<UserDtos.TeamRef> teamRefs = teams.findByUserId(user.getId()).stream()
                .map(t -> new UserDtos.TeamRef(t.getId(), t.getOrganizationId(), t.getName())).toList();
        List<SystemRole> userRoles = roles.findByUserId(user.getId()).stream().map(UserRole::getRole).toList();
        return new UserDtos.UserDetail(
                user.getId(), user.getEmail(), user.getName(), user.getStatus().name(),
                user.getOrganizationId(), orgName, teamRefs, userRoles,
                user.getCreatedAt(), user.getCreatedAt());
    }

    /** 한 페이지 분량의 사용자 요약을 조립한다. 팀·역할은 IN 절 두 번으로 모아 N+1 을 피한다. */
    private List<UserDtos.UserSummary> summariesOf(List<Long> ids) {
        Map<Long, AppUser> byId = new LinkedHashMap<>();
        for (Long id : ids) {
            users.findById(id).ifPresent(u -> byId.put(id, u));
        }

        Map<Long, List<UserDtos.TeamRef>> teamsByUser = new LinkedHashMap<>();
        jdbc.sql("""
                        SELECT ut.user_id, t.id, t.organization_id, t.name
                        FROM user_team ut JOIN team t ON t.id = ut.team_id
                        WHERE ut.user_id IN (:ids)
                        ORDER BY t.name
                        """)
                .param("ids", ids)
                .query((rs, i) -> {
                    teamsByUser.computeIfAbsent(rs.getLong("user_id"), k -> new ArrayList<>())
                            .add(new UserDtos.TeamRef(rs.getLong("id"), rs.getLong("organization_id"), rs.getString("name")));
                    return null;
                }).list();

        Map<Long, List<SystemRole>> rolesByUser = new LinkedHashMap<>();
        jdbc.sql("SELECT user_id, role::text AS role FROM user_role WHERE user_id IN (:ids)")
                .param("ids", ids)
                .query((rs, i) -> {
                    rolesByUser.computeIfAbsent(rs.getLong("user_id"), k -> new ArrayList<>())
                            .add(SystemRole.valueOf(rs.getString("role")));
                    return null;
                }).list();

        List<UserDtos.UserSummary> content = new ArrayList<>();
        for (Long id : ids) {
            AppUser u = byId.get(id);
            if (u == null) continue;
            String orgName = u.getOrganizationId() == null ? null
                    : organizations.findById(u.getOrganizationId()).map(Organization::getName).orElse(null);
            content.add(new UserDtos.UserSummary(
                    u.getId(), u.getEmail(), u.getName(), u.getStatus().name(),
                    u.getOrganizationId(), orgName,
                    teamsByUser.getOrDefault(id, List.of()),
                    rolesByUser.getOrDefault(id, List.of())));
        }
        return content;
    }

    public static class ValidationException extends RuntimeException {
        private final String field;

        public ValidationException(String field, String message) {
            super(message);
            this.field = field;
        }

        public String field() {
            return field;
        }
    }
}

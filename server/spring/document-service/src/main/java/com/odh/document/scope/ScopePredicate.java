package com.odh.document.scope;

import com.odh.common.PermissionScope;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 권한 범위를 SQL 조건으로 옮긴다.
 *
 * 조회 결과를 받아서 거르지 않고 질의 자체에 조건을 넣는 이유는,
 * 페이지네이션과 건수 집계가 권한 범위 안에서 계산되어야 하기 때문이다.
 * 뒤에서 거르면 "전체 114건" 같은 숫자가 권한 밖 문서까지 세게 된다.
 */
public final class ScopePredicate {

    private ScopePredicate() {
    }

    /**
     * @return SQL 조건절과 바인딩 파라미터. 아무 범위도 없으면 항상 거짓인 조건을 준다.
     */
    public static Clause of(PermissionScope scope) {
        return of(scope, "d");
    }

    /**
     * {@code alias}로 별칭 붙인 문서 테이블에 조건을 건다. 한 쿼리에 문서 테이블이
     * 두 번 이상 조인될 때(예: 상충의 source/target 문서를 각각 걸러야 할 때) 쓴다.
     * 파라미터 이름도 alias로 접두해 같은 쿼리 안에서 여러 번 호출해도 충돌하지 않는다.
     *
     * @return SQL 조건절과 바인딩 파라미터. 아무 범위도 없으면 항상 거짓인 조건을 준다.
     */
    public static Clause of(PermissionScope scope, String alias) {
        if (scope.systemAdmin()) {
            return new Clause("TRUE", Map.of());
        }
        if (scope.isEmpty()) {
            return new Clause("FALSE", Map.of());
        }

        List<String> parts = new ArrayList<>();
        Map<String, Object> params = new java.util.HashMap<>();
        String orgParam = alias + "ScopeOrgIds";
        String teamParam = alias + "ScopeTeamIds";
        String docParam = alias + "ScopeDocIds";
        String deniedParam = alias + "ScopeDeniedDocIds";

        if (!scope.organizationIds().isEmpty()) {
            parts.add(alias + ".organization_id IN (:" + orgParam + ")");
            params.put(orgParam, scope.organizationIds());
        }
        if (!scope.teamIds().isEmpty()) {
            parts.add(alias + ".team_id IN (:" + teamParam + ")");
            params.put(teamParam, scope.teamIds());
        }
        if (!scope.documentIds().isEmpty()) {
            parts.add(alias + ".id IN (:" + docParam + ")");
            params.put(docParam, scope.documentIds());
        }

        String allow = "(" + String.join(" OR ", parts) + ")";

        // 차단 정책은 허용보다 우선한다
        if (!scope.deniedDocumentIds().isEmpty()) {
            allow += " AND " + alias + ".id NOT IN (:" + deniedParam + ")";
            params.put(deniedParam, scope.deniedDocumentIds());
        }
        return new Clause(allow, Map.copyOf(params));
    }

    public record Clause(String sql, Map<String, Object> params) {
    }
}

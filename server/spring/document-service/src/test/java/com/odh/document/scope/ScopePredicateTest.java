package com.odh.document.scope;

import com.odh.common.PermissionScope;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 권한 범위 → SQL 조건 변환.
 *
 * DecisionGraphService·ConflictQueryService가 한 쿼리에서 문서 테이블을 두 번(source/target)
 * 조인할 때 별칭별로 독립된 조건·파라미터를 만드는지가 핵심이다 — 파라미터 이름이 겹치면
 * 한쪽 조건이 다른 쪽 값으로 덮어써져 상충의 반대편 문서가 스코프 없이 새어 나간다.
 */
class ScopePredicateTest {

    @Test
    void systemAdmin_seesEverything() {
        var scope = new PermissionScope(Set.of(), Set.of(), Set.of(), Set.of(), true);

        var clause = ScopePredicate.of(scope, "d");

        assertThat(clause.sql()).isEqualTo("TRUE");
        assertThat(clause.params()).isEmpty();
    }

    @Test
    void emptyScope_seesNothing() {
        var clause = ScopePredicate.of(PermissionScope.empty(), "d");

        assertThat(clause.sql()).isEqualTo("FALSE");
        assertThat(clause.params()).isEmpty();
    }

    @Test
    void organizationScope_filtersByAliasedColumn() {
        var scope = new PermissionScope(Set.of(1L, 2L), Set.of(), Set.of(), Set.of(), false);

        var clause = ScopePredicate.of(scope, "sd");

        assertThat(clause.sql()).contains("sd.organization_id IN (:sdScopeOrgIds)");
        assertThat(clause.params()).containsEntry("sdScopeOrgIds", Set.of(1L, 2L));
    }

    @Test
    void deniedDocuments_takePriorityOverAllowedOrganization() {
        var scope = new PermissionScope(Set.of(1L), Set.of(), Set.of(), Set.of(99L), false);

        var clause = ScopePredicate.of(scope, "d");

        assertThat(clause.sql()).contains("AND d.id NOT IN (:dScopeDeniedDocIds)");
        assertThat(clause.params()).containsEntry("dScopeDeniedDocIds", Set.of(99L));
    }

    /** source/target 두 별칭을 같은 쿼리에서 같이 써도 파라미터 이름이 겹치지 않아야 한다. */
    @Test
    void twoAliasesInSameQuery_produceDisjointParamNames() {
        var scope = new PermissionScope(Set.of(1L), Set.of(2L), Set.of(3L), Set.of(4L), false);

        var source = ScopePredicate.of(scope, "sd");
        var target = ScopePredicate.of(scope, "td");

        assertThat(source.params().keySet()).doesNotContainAnyElementsOf(target.params().keySet());
        assertThat(source.sql()).contains("sd.").doesNotContain("td.");
        assertThat(target.sql()).contains("td.").doesNotContain("sd.");
    }

    @Test
    void defaultAlias_isD() {
        var scope = new PermissionScope(Set.of(1L), Set.of(), Set.of(), Set.of(), false);

        assertThat(ScopePredicate.of(scope)).isEqualTo(ScopePredicate.of(scope, "d"));
    }
}

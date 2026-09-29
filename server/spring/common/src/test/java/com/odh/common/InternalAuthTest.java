package com.odh.common;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/** X-User-Roles 헤더 파싱. ConflictController의 관리자 판정에 쓴다. */
class InternalAuthTest {

    @Test
    void commaSeparatedRoles_areTrimmedAndSet() {
        assertThat(InternalAuth.parseRoles("SYSTEM_ADMIN, MEMBER ,TEAM_ADMIN"))
                .containsExactlyInAnyOrder("SYSTEM_ADMIN", "MEMBER", "TEAM_ADMIN");
    }

    @Test
    void nullOrBlank_isEmptySet() {
        assertThat(InternalAuth.parseRoles(null)).isEmpty();
        assertThat(InternalAuth.parseRoles("")).isEmpty();
        assertThat(InternalAuth.parseRoles("   ")).isEmpty();
    }

    @Test
    void emptySegmentsBetweenCommas_areDropped() {
        assertThat(InternalAuth.parseRoles("MEMBER,,")).containsExactly("MEMBER");
    }
}

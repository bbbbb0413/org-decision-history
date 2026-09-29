package com.odh.common;

/**
 * 서비스 간 호출 인증 규약.
 *
 * 사용자 요청은 게이트웨이가 JWT 로 검증하고, 게이트웨이 뒤의 서비스끼리 주고받는 요청은
 * 등록된 API 키로 검증한다. 게이트웨이를 거치지 않은 직접 호출을 막기 위한 장치다.
 */
public final class InternalAuth {

    /** 내부 호출임을 표시하는 API 키 헤더 */
    public static final String API_KEY_HEADER = "X-Internal-Key";

    /** 게이트웨이가 JWT 검증 후 하위 서비스에 넘기는 사용자 식별자 */
    public static final String USER_ID_HEADER = "X-User-Id";

    /** 게이트웨이가 넘기는 사용자 역할 목록. 쉼표로 구분한다 */
    public static final String USER_ROLES_HEADER = "X-User-Roles";

    /** {@link #USER_ROLES_HEADER} 값을 역할 집합으로 파싱한다. */
    public static java.util.Set<String> parseRoles(String rolesHeader) {
        if (rolesHeader == null || rolesHeader.isBlank()) {
            return java.util.Set.of();
        }
        return java.util.Arrays.stream(rolesHeader.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .collect(java.util.stream.Collectors.toUnmodifiableSet());
    }

    private InternalAuth() {
    }
}

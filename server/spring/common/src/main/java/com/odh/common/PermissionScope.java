package com.odh.common;

import java.util.List;
import java.util.Set;

/**
 * 사용자가 읽을 수 있는 범위.
 *
 * auth-service 가 계산해 다른 서비스에 넘긴다. 색인 payload 와 검색 필터, 원본 다운로드가
 * 모두 이 한 가지 형태를 쓴다. 계산 지점이 여러 곳이면 서로 어긋나기 때문이다.
 *
 * @param organizationIds 조직 단위로 허용된 범위
 * @param teamIds         팀 단위로 허용된 범위
 * @param documentIds     문서 단위로 개별 허용된 범위
 * @param deniedDocumentIds 명시적으로 차단된 문서. 허용보다 우선한다
 * @param systemAdmin     전체 범위 관리자 여부
 */
public record PermissionScope(
        Set<Long> organizationIds,
        Set<Long> teamIds,
        Set<Long> documentIds,
        Set<Long> deniedDocumentIds,
        boolean systemAdmin
) {

    public static PermissionScope empty() {
        return new PermissionScope(Set.of(), Set.of(), Set.of(), Set.of(), false);
    }

    /** 아무 범위도 없으면 검색·조회가 항상 0건이다. 질의 자체를 건너뛸 수 있다. */
    public boolean isEmpty() {
        return !systemAdmin
                && organizationIds.isEmpty()
                && teamIds.isEmpty()
                && documentIds.isEmpty();
    }

    /**
     * Qdrant payload 와 로그에 쓰는 문자열 표기.
     * 벡터 저장소는 집합 연산이 제한적이라 미리 문자열로 만들어 둔다.
     */
    public List<String> toScopeKeys() {
        if (systemAdmin) {
            return List.of("all");
        }
        return java.util.stream.Stream.of(
                        organizationIds.stream().map(id -> "org:" + id),
                        teamIds.stream().map(id -> "team:" + id),
                        documentIds.stream().map(id -> "doc:" + id))
                .flatMap(s -> s)
                .sorted()
                .toList();
    }
}

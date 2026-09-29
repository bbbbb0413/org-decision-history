package com.odh.common.web;

import java.util.List;

/**
 * 서비스별로 거의 동일하게 반복되던 페이지 응답 DTO를 하나로 모은 것.
 * `OpsDtos`/`PolicyDtos`/`ConversationDtos`/`IndexDtos` 각각에 있던
 * `Page<T>(content, page, size, totalElements, totalPages)` 정의를 대체한다.
 */
public record PageResponse<T>(List<T> content, int page, int size, long totalElements, int totalPages) {

    /** size==0 이면 totalPages 도 0 — 나눗셈으로 나머지 계산 로직 곳곳에서 반복되던 가드. */
    public static int totalPagesOf(long totalElements, int size) {
        return size == 0 ? 0 : (int) Math.ceil((double) totalElements / size);
    }

    public static <T> PageResponse<T> of(List<T> content, int page, int size, long totalElements) {
        return new PageResponse<>(content, page, size, totalElements, totalPagesOf(totalElements, size));
    }
}

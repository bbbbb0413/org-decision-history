package com.odh.common.web;

/**
 * 목록 조회의 page·size 를 안전한 범위로 고정한다.
 *
 * <p>보정하지 않으면 {@code page=-1} 이 그대로 {@code OFFSET -20} 이 되어 Postgres 가
 * 거부한다 — 사용자 입력 하나로 500 이 나간다. 모든 목록 API 가 이 클래스를 거친다.
 *
 * <p>size 상한도 함께 둔다. 상한이 없으면 {@code size=100000} 같은 요청 하나로
 * 테이블 전체를 메모리에 올리게 만들 수 있다.
 */
public record PageRequest(int page, int size) {

    /** 한 번에 내려줄 수 있는 최대 건수. ConflictQueryService 가 쓰던 값과 같다. */
    public static final int MAX_SIZE = 100;
    /** size 를 0 이나 음수로 보냈을 때 쓸 값. 빈 목록을 돌려주는 것보다 기본값이 낫다. */
    public static final int DEFAULT_SIZE = 20;

    /**
     * 음수 page 는 0 으로, 범위 밖 size 는 기본값·상한으로 맞춘다.
     *
     * <p>400 으로 거절하지 않고 보정하는 쪽을 골랐다 — 이미 배포된 프론트·알림 링크가
     * 보내는 값을 깨뜨리지 않으면서 500 만 없애는 게 목적이다. 잘못된 값을 거절해야 하는
     * 자리라면 컨트롤러에서 {@code @Min} 으로 따로 막는다.
     */
    public static PageRequest of(int page, int size) {
        int safeSize = (size <= 0) ? DEFAULT_SIZE : Math.min(size, MAX_SIZE);
        return new PageRequest(Math.max(0, page), safeSize);
    }

    /** {@code LIMIT :size OFFSET :offset} 에 그대로 넣는다. long 으로 계산해 int 넘침을 막는다. */
    public long offset() {
        return (long) page * size;
    }
}

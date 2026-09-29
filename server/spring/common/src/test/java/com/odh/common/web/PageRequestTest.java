package com.odh.common.web;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class PageRequestTest {

    @Test
    @DisplayName("정상 범위는 그대로 통과한다")
    void keepsValidValues() {
        PageRequest paging = PageRequest.of(3, 50);
        assertThat(paging.page()).isEqualTo(3);
        assertThat(paging.size()).isEqualTo(50);
        assertThat(paging.offset()).isEqualTo(150L);
    }

    @Test
    @DisplayName("음수 page 는 0 이 된다 — 그대로 두면 OFFSET 이 음수가 되어 500 이 났다")
    void clampsNegativePage() {
        assertThat(PageRequest.of(-1, 20).page()).isZero();
        assertThat(PageRequest.of(-1, 20).offset()).isZero();
        assertThat(PageRequest.of(Integer.MIN_VALUE, 20).page()).isZero();
    }

    @Test
    @DisplayName("size 0·음수는 기본값으로, 상한 초과는 상한으로 맞춘다")
    void clampsSize() {
        assertThat(PageRequest.of(0, 0).size()).isEqualTo(PageRequest.DEFAULT_SIZE);
        assertThat(PageRequest.of(0, -5).size()).isEqualTo(PageRequest.DEFAULT_SIZE);
        assertThat(PageRequest.of(0, 100_000).size()).isEqualTo(PageRequest.MAX_SIZE);
        assertThat(PageRequest.of(0, PageRequest.MAX_SIZE).size()).isEqualTo(PageRequest.MAX_SIZE);
    }

    @Test
    @DisplayName("큰 page 에서도 offset 이 int 로 넘치지 않는다")
    void offsetDoesNotOverflow() {
        PageRequest paging = PageRequest.of(Integer.MAX_VALUE, 100);
        // int 로 곱했다면 음수가 나왔을 값이다.
        assertThat(paging.offset()).isEqualTo((long) Integer.MAX_VALUE * 100);
        assertThat(paging.offset()).isPositive();
    }
}

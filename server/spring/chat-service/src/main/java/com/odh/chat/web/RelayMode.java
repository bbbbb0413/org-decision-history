package com.odh.chat.web;

/**
 * 답변 SSE 릴레이 방식 ({@code chat.sse.relay-mode}).
 *
 * POLL   job 마다 블로킹 XREAD 루프(플랫폼 스레드 1개). 롤백·전후 비교용으로 남겨 둔다
 * PUBSUB 인스턴스당 알림 구독 하나 + 연결별 가상 스레드가 Stream 을 XRANGE 로 읽는다
 */
public enum RelayMode {
    POLL,
    PUBSUB
}

package com.odh.chat.relay;

/** 알림 채널 구독. 테스트에서 Redis 없이 바꿔 끼우려고 뗐다. */
public interface RelayChannels {

    /**
     * 구독이 확정된 뒤 돌아온다. {@code onMessage} 는 구독 커넥션의 수신 스레드에서 불린다 —
     * 막히면 그 인스턴스의 모든 알림이 밀리므로 구독자를 깨우는 정도만 해야 한다.
     */
    void subscribe(String channel, Runnable onMessage);

    void unsubscribe(String channel);
}

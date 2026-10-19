package com.odh.chat.relay;

import java.io.IOException;

/** 브라우저 쪽 SSE 연결. {@link StreamSubscriber} 의 스레드 하나만 부른다. */
public interface RelayOutput {

    /** @throws IOException 클라이언트가 끊었다. 오류가 아니라 정상 종료다 */
    void event(RelayRecord record) throws IOException;

    void heartbeat() throws IOException;

    void complete();
}

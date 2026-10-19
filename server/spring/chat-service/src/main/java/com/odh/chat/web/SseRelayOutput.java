package com.odh.chat.web;

import com.odh.chat.relay.RelayOutput;
import com.odh.chat.relay.RelayRecord;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;

/** {@link RelayOutput} 의 SseEmitter 구현. 이벤트 모양은 POLL 경로의 relay 와 같다. */
final class SseRelayOutput implements RelayOutput {

    private final SseEmitter emitter;

    SseRelayOutput(SseEmitter emitter) {
        this.emitter = emitter;
    }

    @Override
    public void event(RelayRecord record) throws IOException {
        emitter.send(SseEmitter.event().id(record.id()).name(record.event()).data(record.data()));
    }

    @Override
    public void heartbeat() throws IOException {
        emitter.send(SseEmitter.event().comment("heartbeat"));
    }

    @Override
    public void complete() {
        emitter.complete();
    }
}

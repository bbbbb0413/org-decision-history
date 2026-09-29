package com.odh.chat.web;

import com.odh.chat.answer.AnswerStatus;
import com.odh.chat.conversation.ConversationDtos;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * SSE 이벤트 전송. {@link AnswerSink}의 HTTP 커넥션 직결 구현체다.
 *
 * 이벤트 순서는 conversation → sources → delta(반복) → done 이다.
 * 참고 문서를 답변 조각보다 먼저 보내 사용자가 근거를 먼저 판단하게 한다.
 */
public class SseSink implements AnswerSink {

    private static final Logger log = LoggerFactory.getLogger(SseSink.class);

    private final SseEmitter emitter;
    private volatile boolean closed;

    public SseSink(SseEmitter emitter) {
        this.emitter = emitter;
    }

    /** job 발행 직후, 처리 시작 전에 1회 보낸다. jobKey 는 재연결 시 이어받는 데 쓰인다. */
    public void conversation(long conversationId, java.util.UUID jobKey) {
        send("conversation", Map.of("conversationId", conversationId, "jobKey", jobKey.toString()));
    }

    @Override
    public void sources(List<ConversationDtos.Source> sources) {
        send("sources", Map.of("sources", sources));
    }

    @Override
    public void delta(String text) {
        send("delta", Map.of("text", text));
    }

    @Override
    public void done(long messageId, AnswerStatus status, String modelName,
                     int latencyMs, List<String> suggestions, Instant resumeAt) {
        Map<String, Object> data = new HashMap<>();
        data.put("messageId", messageId);
        data.put("answerStatus", status.name());
        data.put("modelName", modelName == null ? "" : modelName);
        data.put("latencyMs", latencyMs);
        data.put("suggestions", suggestions);
        data.put("resumeAt", resumeAt == null ? null : resumeAt.toString());
        send("done", data);
    }

    @Override
    public void error(String code, String message) {
        send("error", Map.of("code", code, "message", message));
    }

    /** 연결 유지용. 15초마다 보낸다. 중간 프록시가 유휴 연결을 끊지 않게 한다. */
    public void heartbeat() {
        if (closed) return;
        try {
            emitter.send(SseEmitter.event().comment("heartbeat"));
        } catch (IOException e) {
            closed = true;
        }
    }

    public boolean isClosed() {
        return closed;
    }

    private void send(String name, Object data) {
        if (closed) return;
        try {
            emitter.send(SseEmitter.event().name(name).data(data));
        } catch (IOException e) {
            // 사용자가 화면을 닫으면 여기로 온다. 오류가 아니라 정상 종료다.
            closed = true;
            log.debug("SSE 연결이 끊겼다: {}", e.getMessage());
        }
    }
}

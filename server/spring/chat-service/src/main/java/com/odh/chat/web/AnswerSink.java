package com.odh.chat.web;

import com.odh.chat.answer.AnswerStatus;
import com.odh.chat.conversation.ConversationDtos;

import java.time.Instant;
import java.util.List;

/**
 * {@link AskService#ask}가 처리 진행 상황을 내보내는 통로.
 *
 * 이벤트 순서는 sources → delta(반복) → done 이다.
 * 구현체는 두 가지다: HTTP 요청을 직접 들고 있는 커넥션에 쓰는 {@link SseSink},
 * Kafka 워커가 Redis Stream 으로 발행하는 RedisStreamAnswerSink.
 */
public interface AnswerSink {

    void sources(List<ConversationDtos.Source> sources);

    void delta(String text);

    /** resumeAt 없이 끝나는 대부분의 경우를 위한 편의 메서드. */
    default void done(long messageId, AnswerStatus status, String modelName, int latencyMs, List<String> suggestions) {
        done(messageId, status, modelName, latencyMs, suggestions, null);
    }

    /** @param resumeAt RATE_LIMITED 이고 재개 예정 시각을 알 때만 채운다. */
    void done(long messageId, AnswerStatus status, String modelName, int latencyMs, List<String> suggestions,
              Instant resumeAt);

    void error(String code, String message);
}

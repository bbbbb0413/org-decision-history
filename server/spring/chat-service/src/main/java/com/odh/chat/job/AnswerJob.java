package com.odh.chat.job;

import java.util.UUID;

/** 답변 생성 작업 1건. Kafka 로는 {@code jobKey} 만 흘려보내고, 나머지는 여기서 다시 읽는다. */
public record AnswerJob(
        UUID jobKey,
        long conversationId,
        long userId,
        String question,
        JobStatus status,
        Long assistantMessageId
) {
}

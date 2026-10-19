package com.odh.auth.metrics;

import java.util.Locale;
import java.util.Optional;

/**
 * 답변 평가값. {@code chat.answer_feedback.rating} 의 CHECK 제약(chat-service V1)과 같다.
 *
 * <p>목록 필터는 문자열로 받고 대소문자를 가리지 않는다. 허용값이 아니면 필터 없이 전체를 돌려준다 — 잘못된
 * 필터값에 400 을 내지 않는 지금 동작을 지키려고 enum 으로 직접 받지 않는다. 임의 문자열이 SQL 비교로
 * 흘러가지 않게 하는 허용 목록이 이 enum 이다.
 */
public enum FeedbackRating {
    HELPFUL,
    NOT_HELPFUL;

    public static Optional<FeedbackRating> parse(String value) {
        if (value == null || value.isBlank()) {
            return Optional.empty();
        }
        String normalized = value.trim().toUpperCase(Locale.ROOT);
        for (FeedbackRating r : values()) {
            if (r.name().equals(normalized)) {
                return Optional.of(r);
            }
        }
        return Optional.empty();
    }
}

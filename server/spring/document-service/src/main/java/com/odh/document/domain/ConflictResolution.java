package com.odh.document.domain;

import java.util.Locale;
import java.util.Optional;

/**
 * 사용자가 상충을 직접 닫을 때 지정할 수 있는 상태(PATCH /api/conflicts/{id}). 프론트(ConflictActionModal)가 주는 값과 같다.
 *
 * <p>{@code decision_conflicts.status} 의 나머지 값(OPEN·SUPERSEDED)은 시스템만 정한다. 요청 필드는 문자열로 받고
 * 대소문자를 가리지 않는다 — enum 으로 직접 받으면 소문자 요청이 역직렬화 단계에서 400 이 되어 지금 동작이 바뀐다.
 */
public enum ConflictResolution {
    RESOLVED,
    DISMISSED;

    public static Optional<ConflictResolution> parse(String value) {
        if (value == null || value.isBlank()) {
            return Optional.empty();
        }
        String normalized = value.trim().toUpperCase(Locale.ROOT);
        for (ConflictResolution r : values()) {
            if (r.name().equals(normalized)) {
                return Optional.of(r);
            }
        }
        return Optional.empty();
    }
}

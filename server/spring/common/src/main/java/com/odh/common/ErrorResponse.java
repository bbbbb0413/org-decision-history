package com.odh.common;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.springframework.web.bind.MethodArgumentNotValidException;

import java.time.Instant;
import java.util.List;

/**
 * 모든 실패 응답이 공유하는 형태.
 *
 * @param code          기계 판독용 오류 코드
 * @param message       사용자에게 보여줄 안내
 * @param details       필드 단위 오류. 폼 화면에서 항목별로 표시한다
 * @param queuePosition LLM 한도 초과 시에만 채운다
 * @param resumeAt      LLM 한도 초과 시의 재개 예정 시각
 * @param correlationId 요청 추적 ID
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ErrorResponse(
        String code,
        String message,
        List<FieldError> details,
        Integer queuePosition,
        Instant resumeAt,
        String correlationId
) {

    public record FieldError(String field, String message) {
    }

    public static ErrorResponse of(String code, String message, String correlationId) {
        return new ErrorResponse(code, message, null, null, null, correlationId);
    }

    public static ErrorResponse of(String code, String message, List<FieldError> details, String correlationId) {
        return new ErrorResponse(code, message, details, null, null, correlationId);
    }

    /** `@Valid` 바인딩 실패 시 필드별 사유를 뽑아낸다. 여러 서비스의 ExceptionHandler가 반복하던 로직. */
    public static List<FieldError> fieldErrorsOf(MethodArgumentNotValidException e) {
        return e.getBindingResult().getFieldErrors().stream()
                .map(f -> new FieldError(f.getField(), f.getDefaultMessage()))
                .toList();
    }
}

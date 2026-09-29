package com.odh.common.web;

import com.odh.common.Correlation;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.util.List;

/**
 * 프레임워크가 던지는 요청 오류를 프로젝트 공통 {@link ErrorResponse} 형태로 맞춘다.
 *
 * <p>처리하지 않으면 Spring 기본 형태
 * ({@code {"timestamp","status","error","path"}})가 그대로 나간다. 프론트는
 * {@code HttpError.body.message} 를 읽어 화면에 띄우므로(client.ts), 그 형태가 오면
 * 사유 없는 빈 오류가 뜬다. 해당 경로: 깨진 JSON, 잘못된 enum 값, 잘못된 날짜,
 * 숫자 아닌 경로 변수, 필수 쿼리 파라미터 누락.
 *
 * <p>각 서비스의 도메인 예외 핸들러({@code AuthExceptionHandler} 등)와 함께 동작한다.
 * 서비스별 {@code @RestControllerAdvice} 가 더 구체적인 예외를 먼저 잡고, 여기서는
 * 프레임워크 공통 예외만 받는다. 세 서비스가 같은 코드를 세 번 두지 않게 common 에 둔다.
 *
 * <p>common 은 각 서비스의 컴포넌트 스캔 대상이 아니다(게이트웨이가 서블릿 전용 설정을
 * 집어가지 않게 하려는 의도다). 그래서 서블릿 서비스 세 곳의 설정에서
 * {@code @Import} 로 직접 등록한다 — {@code AuthConfiguration},
 * {@code DocumentServiceConfiguration}, {@code ChatServiceConfiguration}.
 * WebFlux 인 게이트웨이는 이 설정을 가져가지 않으므로 여기 예외들과 무관하다.
 */
@RestControllerAdvice
// 서비스별 핸들러가 먼저 잡게 하고 여기서는 남는 것만 받는다. document-service 는
// MethodArgumentNotValidException 에 자기 문구를 쓰는데, 순서를 주지 않으면 어느 쪽이
// 이길지 불분명해진다.
@Order(Ordered.LOWEST_PRECEDENCE)
public class CommonExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(CommonExceptionHandler.class);

    /** `@Valid` 바인딩 실패. 필드별 사유를 함께 돌려줘 폼 화면이 항목별로 표시할 수 있게 한다. */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> onValidation(MethodArgumentNotValidException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "입력값을 확인해 주세요.",
                ErrorResponse.fieldErrorsOf(e), Correlation.currentId()));
    }

    /** 경로 변수·쿼리 파라미터에 건 제약(@Min 등) 위반. */
    @ExceptionHandler(HandlerMethodValidationException.class)
    public ResponseEntity<ErrorResponse> onHandlerValidation() {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "입력값을 확인해 주세요.", Correlation.currentId()));
    }

    /**
     * 본문을 읽지 못했다 — 깨진 JSON, 알 수 없는 enum 값, 파싱 불가한 날짜 등.
     *
     * <p>사유를 그대로 내보내지 않는다. Jackson 메시지에는 패키지명·필드 경로 같은
     * 내부 구조가 섞여 있어 그대로 노출하면 공격자에게 단서가 된다. 로그에만 남긴다.
     */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ErrorResponse> onUnreadableBody(HttpMessageNotReadableException e) {
        log.debug("요청 본문을 읽지 못했다: {}", e.getMessage());
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "요청 본문 형식이 올바르지 않습니다.", Correlation.currentId()));
    }

    /** 경로 변수·쿼리 파라미터의 타입이 맞지 않는다(예: `/api/documents/abc`). */
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ErrorResponse> onTypeMismatch(MethodArgumentTypeMismatchException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "요청 값의 형식이 올바르지 않습니다.",
                List.of(new ErrorResponse.FieldError(e.getName(), "형식이 올바르지 않습니다")),
                Correlation.currentId()));
    }

    /** 필수 쿼리 파라미터 누락(예: `GET /api/timeline` 의 topic). */
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<ErrorResponse> onMissingParam(MissingServletRequestParameterException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "필수 요청 값이 빠졌습니다.",
                List.of(new ErrorResponse.FieldError(e.getParameterName(), "필수 항목입니다")),
                Correlation.currentId()));
    }

    /**
     * 게이트웨이가 주입하는 헤더가 없다(X-User-Id 등). 내부 호출 경로가 잘못된 것이지
     * 사용자 입력 문제가 아니므로 401 로 돌린다 — 게이트웨이를 거치지 않은 직접 호출이다.
     */
    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ErrorResponse> onMissingHeader(MissingRequestHeaderException e) {
        log.warn("필수 헤더 누락: {}", e.getHeaderName());
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(ErrorResponse.of(
                ErrorCode.INTERNAL_AUTH_FAILED, "인증 정보가 없습니다.", Correlation.currentId()));
    }

    /** 허용되지 않은 메서드. */
    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ErrorResponse> onMethodNotAllowed() {
        return ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED).body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "지원하지 않는 요청 방식입니다.", Correlation.currentId()));
    }
}

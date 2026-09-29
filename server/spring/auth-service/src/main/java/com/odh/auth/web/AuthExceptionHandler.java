package com.odh.auth.web;

import com.odh.common.Correlation;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.List;

@RestControllerAdvice
public class AuthExceptionHandler {

    @ExceptionHandler(AuthService.InvalidCredentialsException.class)
    public ResponseEntity<ErrorResponse> onInvalidCredentials() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(ErrorResponse.of(
                ErrorCode.INVALID_CREDENTIALS,
                "이메일 또는 비밀번호가 올바르지 않습니다.",
                Correlation.currentId()));
    }

    /**
     * 재발급 토큰이 읽히지 않는 경우. 사유(서명 불일치·만료·jti 없음·주체 불일치)를 구분해
     * 주지 않는다 — 구분이 곧 어디까지 통과했는지 알려주는 단서가 된다. 로그인 실패와 같은 방침이다.
     */
    @ExceptionHandler(com.odh.auth.jwt.RefreshTokenReader.InvalidRefreshTokenException.class)
    public ResponseEntity<ErrorResponse> onInvalidRefreshToken() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(ErrorResponse.of(
                ErrorCode.TOKEN_INVALID,
                "인증이 만료되었습니다. 다시 로그인해 주세요.",
                Correlation.currentId()));
    }

    @ExceptionHandler(AuthService.SamePasswordException.class)
    public ResponseEntity<ErrorResponse> onSamePassword() {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED,
                "입력값을 확인해 주세요.",
                List.of(new ErrorResponse.FieldError("newPassword", "지금 쓰는 비밀번호와 다른 값을 입력해 주세요.")),
                Correlation.currentId()));
    }

    @ExceptionHandler(com.odh.auth.policy.PolicyService.ScopeViolationException.class)
    public ResponseEntity<ErrorResponse> onScopeViolation() {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(ErrorResponse.of(
                ErrorCode.SCOPE_VIOLATION,
                "관리 범위 밖의 요청입니다. 변경할 수 없습니다.",
                Correlation.currentId()));
    }

    @ExceptionHandler(AuthService.UserNotFoundException.class)
    public ResponseEntity<ErrorResponse> onUserNotFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                ErrorCode.NOT_FOUND, "사용자를 찾을 수 없습니다.", Correlation.currentId()));
    }

    @ExceptionHandler(com.odh.auth.user.TeamNotificationChannelService.TeamNotFoundException.class)
    public ResponseEntity<ErrorResponse> onTeamNotFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                ErrorCode.NOT_FOUND, "팀을 찾을 수 없습니다.", Correlation.currentId()));
    }

    /** 허용 목록 밖 스트림 키는 존재 여부를 알려주지 않고 404 로 닫는다. */
    @ExceptionHandler(com.odh.auth.ops.OpsController.UnknownStreamException.class)
    public ResponseEntity<ErrorResponse> onUnknownStream() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                ErrorCode.NOT_FOUND, "알 수 없는 스트림입니다.", Correlation.currentId()));
    }

    @ExceptionHandler(com.odh.auth.user.UserManagementService.ValidationException.class)
    public ResponseEntity<ErrorResponse> onUserValidation(
            com.odh.auth.user.UserManagementService.ValidationException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "입력값을 확인해 주세요.",
                List.of(new ErrorResponse.FieldError(e.field(), e.getMessage())),
                Correlation.currentId()));
    }

    /** 필수 항목 누락은 필드 단위로 사유를 돌려준다. 폼 화면이 항목별로 표시한다. */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> onValidation(MethodArgumentNotValidException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED,
                "입력값을 확인해 주세요.",
                ErrorResponse.fieldErrorsOf(e),
                Correlation.currentId()));
    }
}

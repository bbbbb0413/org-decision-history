package com.odh.document.web;

import com.odh.common.Correlation;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import com.odh.document.importing.ImportService;
import com.odh.document.service.ConflictQueryService;
import com.odh.document.service.DocumentCommandService;
import com.odh.document.storage.FileStorageService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;

import java.util.List;

@RestControllerAdvice
public class DocumentExceptionHandler {

    @ExceptionHandler(DocumentCommandService.DocumentNotFoundException.class)
    public ResponseEntity<ErrorResponse> onNotFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                ErrorCode.NOT_FOUND, "문서를 찾을 수 없습니다.", Correlation.currentId()));
    }

    @ExceptionHandler(DocumentCommandService.VersionConflictException.class)
    public ResponseEntity<ErrorResponse> onConflict(DocumentCommandService.VersionConflictException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(ErrorResponse.of(
                "VERSION_CONFLICT",
                "다른 사용자가 먼저 수정했습니다. 현재 버전은 %d 입니다.".formatted(e.currentVersion),
                Correlation.currentId()));
    }

    /** 필수 항목 누락은 필드 단위로 사유를 돌려준다. 폼 화면이 항목별로 표시한다. */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> onValidation(MethodArgumentNotValidException e) {
        List<ErrorResponse.FieldError> details = ErrorResponse.fieldErrorsOf(e);
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED,
                "저장하지 못했습니다. 필수 항목 %d개를 입력해 주세요.".formatted(details.size()),
                details, Correlation.currentId()));
    }

    @ExceptionHandler({FileStorageService.FileTooLargeException.class, MaxUploadSizeExceededException.class})
    public ResponseEntity<ErrorResponse> onTooLarge() {
        return ResponseEntity.status(HttpStatus.PAYLOAD_TOO_LARGE).body(ErrorResponse.of(
                "FILE_TOO_LARGE", "파일 크기 상한 50 MB 를 초과했습니다.", Correlation.currentId()));
    }

    @ExceptionHandler(FileStorageService.UnsupportedFileTypeException.class)
    public ResponseEntity<ErrorResponse> onUnsupported(FileStorageService.UnsupportedFileTypeException e) {
        return ResponseEntity.status(HttpStatus.UNSUPPORTED_MEDIA_TYPE).body(ErrorResponse.of(
                "UNSUPPORTED_FILE_TYPE",
                "지원하지 않는 파일 형식입니다. md, txt, PDF, docx, pptx, xlsx 만 올릴 수 있습니다.",
                Correlation.currentId()));
    }

    @ExceptionHandler(ImportService.ValidationException.class)
    public ResponseEntity<ErrorResponse> onImportValidation(ImportService.ValidationException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "입력값을 확인해 주세요.",
                List.of(new ErrorResponse.FieldError(e.field(), e.getMessage())), Correlation.currentId()));
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<ErrorResponse> onMissingParam(MissingServletRequestParameterException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "필수 항목이 빠졌습니다: " + e.getParameterName(),
                List.of(new ErrorResponse.FieldError(e.getParameterName(), "필수 항목입니다")), Correlation.currentId()));
    }

    @ExceptionHandler(MissingServletRequestPartException.class)
    public ResponseEntity<ErrorResponse> onMissingPart(MissingServletRequestPartException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "필수 항목이 빠졌습니다: " + e.getRequestPartName(),
                List.of(new ErrorResponse.FieldError(e.getRequestPartName(), "필수 항목입니다")), Correlation.currentId()));
    }

    @ExceptionHandler(ImportService.ImportJobNotFoundException.class)
    public ResponseEntity<ErrorResponse> onImportNotFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                ErrorCode.NOT_FOUND, "가져오기 작업을 찾을 수 없습니다.", Correlation.currentId()));
    }

    @ExceptionHandler(ImportService.NoRetryableItemsException.class)
    public ResponseEntity<ErrorResponse> onNoRetryable() {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(ErrorResponse.of(
                "NOT_RETRYABLE", "재시도 가능한 실패 건이 없습니다.", Correlation.currentId()));
    }

    /** 없는 상충과 권한 범위 밖 상충을 구분하지 않는다 */
    @ExceptionHandler(ConflictQueryService.ConflictNotFoundException.class)
    public ResponseEntity<ErrorResponse> onConflictNotFound() {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                ErrorCode.NOT_FOUND, "상충 정보를 찾을 수 없습니다.", Correlation.currentId()));
    }

    @ExceptionHandler(ConflictQueryService.InvalidStatusException.class)
    public ResponseEntity<ErrorResponse> onInvalidConflictStatus(ConflictQueryService.InvalidStatusException e) {
        return ResponseEntity.badRequest().body(ErrorResponse.of(
                ErrorCode.VALIDATION_FAILED, "허용되지 않는 상태값입니다: " + e.status, Correlation.currentId()));
    }

    @ExceptionHandler(ConflictController.ForbiddenException.class)
    public ResponseEntity<ErrorResponse> onForbidden() {
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(ErrorResponse.of(
                ErrorCode.SCOPE_VIOLATION, "상충 상태를 변경할 권한이 없습니다.", Correlation.currentId()));
    }
}

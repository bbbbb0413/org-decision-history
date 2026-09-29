package com.odh.common;

/** 서비스 전체가 쓰는 오류 코드를 한곳에 모은다. */
public final class ErrorCode {

    public static final String VALIDATION_FAILED   = "VALIDATION_FAILED";
    public static final String INVALID_CREDENTIALS = "INVALID_CREDENTIALS";
    public static final String TOKEN_INVALID       = "TOKEN_INVALID";
    public static final String TOKEN_EXPIRED       = "TOKEN_EXPIRED";
    public static final String INTERNAL_AUTH_FAILED= "INTERNAL_AUTH_FAILED";
    public static final String SCOPE_VIOLATION     = "SCOPE_VIOLATION";
    public static final String NOT_FOUND           = "NOT_FOUND";
    public static final String NOT_RETRYABLE       = "NOT_RETRYABLE";
    public static final String LLM_RATE_LIMITED    = "LLM_RATE_LIMITED";
    public static final String DOWNLOAD_URL_EXPIRED= "DOWNLOAD_URL_EXPIRED";

    private ErrorCode() {
    }
}

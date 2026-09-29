package com.odh.common;

import org.slf4j.MDC;

/**
 * 상관관계 ID 규약.
 * 게이트웨이가 요청마다 하나를 발급해 헤더로 하위 서비스에 전파하고,
 * 각 서비스는 이 값을 로그 MDC 에 실어 하나의 요청을 서비스 경계 너머로 추적한다.
 */
public final class Correlation {

    /** 게이트웨이가 발급해 전파하는 헤더 이름 */
    public static final String HEADER = "X-Correlation-Id";

    /** 로그 MDC 키. logback 패턴에서 %X{correlationId} 로 참조한다 */
    public static final String MDC_KEY = "correlationId";

    private Correlation() {
    }

    /** 로그와 감사 기록에 쓰는 표기. 사람이 눈으로 구분하기 쉽도록 접두사를 붙인다. */
    public static String newId() {
        return "req_" + java.util.UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    }

    /** 현재 요청의 상관관계 ID. 필터가 미리 채워둔 MDC 값을 그대로 읽는다. */
    public static String currentId() {
        return MDC.get(MDC_KEY);
    }
}

package com.odh.chat.answer;

/** DB 의 answer_status enum 과 값이 같다. */
public enum AnswerStatus {
    /** 정상 생성 */
    OK,
    /** 검색 결과 0건. LLM 을 호출하지 않았다 */
    NO_EVIDENCE,
    /** 근거가 얇아 불확실성을 명시한 답변 */
    LOW_CONFIDENCE,
    /** 경량 모델로 전환한 간이 모드 */
    DEGRADED,
    /** 두 모델 모두 한도를 초과해 대기 */
    RATE_LIMITED
}

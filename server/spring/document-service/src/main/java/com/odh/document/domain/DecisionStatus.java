package com.odh.document.domain;

/** DB 의 decision_status enum 과 값이 같다. 현재 적용·변경·폐기·미확정 */
public enum DecisionStatus {
    CURRENT,
    CHANGED,
    DEPRECATED,
    UNDECIDED
}

package com.odh.document.domain;

/**
 * 상충 심각도 등급. {@code decision_conflicts.severity_level} 과 값이 같다(V6 의 CHECK 제약).
 *
 * <p>governance-worker 가 기록할 때 점수와 그때의 구간으로 정해 저장한다. 이 서비스는 점수에서
 * 등급을 계산하지 않는다 — 구간은 governance-worker({@code app/severity.py}) 한 곳에만 있다.
 */
public enum ConflictSeverityLevel {
    CRITICAL,
    HIGH,
    MEDIUM
}

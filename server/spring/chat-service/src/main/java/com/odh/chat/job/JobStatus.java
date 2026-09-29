package com.odh.chat.job;

/** 답변 생성 작업의 처리 진행 상태. DB 컬럼 chat.answer_job.status 와 1:1 대응한다. */
public enum JobStatus {
    PENDING, RUNNING, COMPLETED, FAILED
}

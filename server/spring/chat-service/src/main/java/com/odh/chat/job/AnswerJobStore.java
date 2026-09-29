package com.odh.chat.job;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;
import java.util.UUID;

/** 답변 생성 작업의 접수·상태 전이 (chat.answer_job). */
@Component
public class AnswerJobStore {

    private final JdbcClient jdbc;

    public AnswerJobStore(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** job 을 PENDING 으로 접수하고 jobKey 를 발급한다. Kafka 로는 이 키만 흘려보낸다. */
    @Transactional
    public UUID createPending(long conversationId, long userId, String question) {
        UUID jobKey = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO chat.answer_job (job_key, conversation_id, user_id, question, status)
                        VALUES (CAST(:jobKey AS uuid), :conversationId, :userId, :question, 'PENDING')
                        """)
                .param("jobKey", jobKey.toString())
                .param("conversationId", conversationId)
                .param("userId", userId)
                .param("question", question)
                .update();
        return jobKey;
    }

    @Transactional(readOnly = true)
    public Optional<AnswerJob> findByKey(UUID jobKey) {
        return jdbc.sql("""
                        SELECT job_key, conversation_id, user_id, question,
                               status::text AS status, assistant_message_id
                        FROM chat.answer_job
                        WHERE job_key = CAST(:jobKey AS uuid)
                        """)
                .param("jobKey", jobKey.toString())
                .query((rs, i) -> new AnswerJob(
                        UUID.fromString(rs.getString("job_key")),
                        rs.getLong("conversation_id"),
                        rs.getLong("user_id"),
                        rs.getString("question"),
                        JobStatus.valueOf(rs.getString("status")),
                        (Long) rs.getObject("assistant_message_id")))
                .optional();
    }

    @Transactional
    public void markRunning(UUID jobKey) {
        setStatus(jobKey, JobStatus.RUNNING, null);
    }

    @Transactional
    public void markCompleted(UUID jobKey, long assistantMessageId) {
        setStatus(jobKey, JobStatus.COMPLETED, assistantMessageId);
    }

    @Transactional
    public void markFailed(UUID jobKey) {
        setStatus(jobKey, JobStatus.FAILED, null);
    }

    private void setStatus(UUID jobKey, JobStatus status, Long assistantMessageId) {
        jdbc.sql("""
                        UPDATE chat.answer_job
                        SET status = CAST(:status AS chat.job_status),
                            assistant_message_id = COALESCE(:assistantMessageId, assistant_message_id),
                            updated_at = now()
                        WHERE job_key = CAST(:jobKey AS uuid)
                        """)
                .param("status", status.name())
                .param("assistantMessageId", assistantMessageId)
                .param("jobKey", jobKey.toString())
                .update();
    }
}

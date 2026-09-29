package com.odh.chat.conversation;

import com.odh.chat.answer.AnswerStatus;
import com.odh.chat.search.SearchHit;
import com.odh.common.web.PageRequest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 대화와 메시지 저장. 본인 대화만 다룬다. */
@Component
public class ConversationStore {

    private final JdbcClient jdbc;

    public ConversationStore(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Transactional
    public long createConversation(long ownerId, String title) {
        return jdbc.sql("""
                        INSERT INTO chat.conversation (owner_id, title)
                        VALUES (:ownerId, :title) RETURNING id
                        """)
                .param("ownerId", ownerId)
                .param("title", title)
                .query(Long.class).single();
    }

    @Transactional(readOnly = true)
    public List<ConversationDtos.Conversation> list(long ownerId, PageRequest paging) {
        return jdbc.sql("""
                        SELECT id, title, created_at, updated_at
                        FROM chat.conversation
                        WHERE owner_id = :ownerId AND deleted_at IS NULL
                        ORDER BY updated_at DESC
                        LIMIT :limit OFFSET :offset
                        """)
                .param("ownerId", ownerId).param("limit", paging.size()).param("offset", paging.offset())
                .query((rs, i) -> new ConversationDtos.Conversation(
                        rs.getLong("id"), rs.getString("title"),
                        rs.getTimestamp("created_at").toInstant(),
                        rs.getTimestamp("updated_at").toInstant()))
                .list();
    }

    @Transactional(readOnly = true)
    public long countConversations(long ownerId) {
        return jdbc.sql("SELECT count(*) FROM chat.conversation WHERE owner_id = :ownerId AND deleted_at IS NULL")
                .param("ownerId", ownerId).query(Long.class).single();
    }

    /** 소유자 확인을 겸한다. 남의 대화는 없는 것으로 본다. */
    @Transactional(readOnly = true)
    public Optional<ConversationDtos.Conversation> findOwned(long conversationId, long ownerId) {
        return jdbc.sql("""
                        SELECT id, title, created_at, updated_at
                        FROM chat.conversation
                        WHERE id = :id AND owner_id = :ownerId AND deleted_at IS NULL
                        """)
                .param("id", conversationId).param("ownerId", ownerId)
                .query((rs, i) -> new ConversationDtos.Conversation(
                        rs.getLong("id"), rs.getString("title"),
                        rs.getTimestamp("created_at").toInstant(),
                        rs.getTimestamp("updated_at").toInstant()))
                .optional();
    }

    @Transactional
    public boolean softDelete(long conversationId, long ownerId) {
        return jdbc.sql("""
                        UPDATE chat.conversation SET deleted_at = now()
                        WHERE id = :id AND owner_id = :ownerId AND deleted_at IS NULL
                        """)
                .param("id", conversationId).param("ownerId", ownerId).update() > 0;
    }

    @Transactional(readOnly = true)
    public List<ConversationDtos.Message> messages(long conversationId) {
        return jdbc.sql("""
                        SELECT m.id, m.conversation_id, m.role::text AS role, m.content,
                               m.model_name, m.answer_status::text AS answer_status, m.latency_ms,
                               m.created_at,
                               (SELECT count(*) FROM chat.answer_source s WHERE s.message_id = m.id) AS source_count
                        FROM chat.message m
                        WHERE m.conversation_id = :id
                        ORDER BY m.created_at, m.id
                        """)
                .param("id", conversationId)
                .query((rs, i) -> new ConversationDtos.Message(
                        rs.getLong("id"), rs.getLong("conversation_id"), rs.getString("role"),
                        rs.getString("content"), rs.getString("model_name"), rs.getString("answer_status"),
                        (Integer) rs.getObject("latency_ms"), rs.getInt("source_count"),
                        rs.getTimestamp("created_at").toInstant()))
                .list();
    }

    /** 재연결(resume) 시, 이미 끝난 job의 결과를 done 이벤트로 다시 알려주는 데 쓴다. */
    @Transactional(readOnly = true)
    public Optional<ConversationDtos.Message> findMessage(long messageId) {
        return jdbc.sql("""
                        SELECT m.id, m.conversation_id, m.role::text AS role, m.content,
                               m.model_name, m.answer_status::text AS answer_status, m.latency_ms,
                               m.created_at,
                               (SELECT count(*) FROM chat.answer_source s WHERE s.message_id = m.id) AS source_count
                        FROM chat.message m
                        WHERE m.id = :id
                        """)
                .param("id", messageId)
                .query((rs, i) -> new ConversationDtos.Message(
                        rs.getLong("id"), rs.getLong("conversation_id"), rs.getString("role"),
                        rs.getString("content"), rs.getString("model_name"), rs.getString("answer_status"),
                        (Integer) rs.getObject("latency_ms"), rs.getInt("source_count"),
                        rs.getTimestamp("created_at").toInstant()))
                .optional();
    }

    @Transactional
    public long appendUserMessage(long conversationId, String content, String rewrittenQuery) {
        long id = jdbc.sql("""
                        INSERT INTO chat.message (conversation_id, role, content, rewritten_query)
                        VALUES (:conversationId, 'USER', :content, :rewritten) RETURNING id
                        """)
                .param("conversationId", conversationId)
                .param("content", content)
                .param("rewritten", rewrittenQuery)
                .query(Long.class).single();
        touch(conversationId);
        return id;
    }

    @Transactional
    public long appendAssistantMessage(long conversationId, String content, String modelName,
                                       AnswerStatus status, int latencyMs, List<SearchHit> sources) {
        long messageId = jdbc.sql("""
                        INSERT INTO chat.message
                            (conversation_id, role, content, model_name, answer_status, latency_ms)
                        VALUES (:conversationId, 'ASSISTANT', :content, :modelName,
                                CAST(:status AS chat.answer_status), :latencyMs)
                        RETURNING id
                        """)
                .param("conversationId", conversationId)
                .param("content", content)
                .param("modelName", modelName)
                .param("status", status.name())
                .param("latencyMs", latencyMs)
                .query(Long.class).single();

        for (int rank = 0; rank < sources.size(); rank++) {
            SearchHit hit = sources.get(rank);
            jdbc.sql("""
                            INSERT INTO chat.answer_source
                                (message_id, document_id, document_version, chunk_no, similarity, rank)
                            VALUES (:messageId, :documentId, :version, :chunkNo, :similarity, :rank)
                            """)
                    .param("messageId", messageId)
                    .param("documentId", hit.documentId())
                    .param("version", hit.documentVersion())
                    .param("chunkNo", hit.chunkNo())
                    // RRF 병합 점수(hit.score())가 아니라 실제 코사인 유사도를 남긴다. 전문검색으로만
                    // 걸린 근거는 유사도가 없으므로 NULL — chat.answer_source.similarity 는 nullable 이다.
                    .param("similarity", hit.vectorSimilarity() == null ? null
                            : Math.min(0.9999, Math.max(0, hit.vectorSimilarity())))
                    .param("rank", rank + 1)
                    .update();
        }
        touch(conversationId);
        return messageId;
    }

    /** 첫 질문으로 대화 제목을 정한다. 목록에서 무슨 대화였는지 알아보게 하기 위해서다. */
    @Transactional
    public void renameIfUntitled(long conversationId, String firstQuestion) {
        jdbc.sql("""
                        UPDATE chat.conversation SET title = :title
                        WHERE id = :id AND title = '새 대화'
                        """)
                .param("id", conversationId)
                .param("title", firstQuestion.length() > 60 ? firstQuestion.substring(0, 60) : firstQuestion)
                .update();
    }

    private void touch(long conversationId) {
        jdbc.sql("UPDATE chat.conversation SET updated_at = now() WHERE id = :id")
                .param("id", conversationId).update();
    }

    @Transactional(readOnly = true)
    public Optional<Long> ownerOfMessage(long messageId) {
        return jdbc.sql("""
                        SELECT c.owner_id FROM chat.message m
                        JOIN chat.conversation c ON c.id = m.conversation_id
                        WHERE m.id = :id
                        """)
                .param("id", messageId).query(Long.class).optional();
    }

    @Transactional
    public void upsertFeedback(long messageId, long userId, String rating, String reason) {
        jdbc.sql("""
                        INSERT INTO chat.answer_feedback (message_id, user_id, rating, reason)
                        VALUES (:messageId, :userId, CAST(:rating AS chat.feedback_rating), :reason)
                        ON CONFLICT (message_id) DO UPDATE SET
                            rating = EXCLUDED.rating,
                            reason = EXCLUDED.reason,
                            updated_at = now()
                        """)
                .param("messageId", messageId).param("userId", userId)
                .param("rating", rating).param("reason", reason)
                .update();
    }

    /** 답변에 쓴 출처를 다시 읽는다. 본문은 색인에서 가져온다. */
    @Transactional(readOnly = true)
    public List<SearchHit> sourcesOfMessage(long messageId) {
        return jdbc.sql("""
                        SELECT s.document_id, s.document_version, s.chunk_no, s.similarity,
                               c.content, c.section_path, c.chunk_tag::text AS chunk_tag
                        FROM chat.answer_source s
                        LEFT JOIN indexing.document_chunk c
                               ON c.document_id = s.document_id
                              AND c.document_version = s.document_version
                              AND c.chunk_no = s.chunk_no
                        WHERE s.message_id = :messageId
                        ORDER BY s.rank
                        """)
                .param("messageId", messageId)
                .query((rs, i) -> {
                    // similarity 는 NUMERIC(5,4) 라 pgjdbc 가 BigDecimal 로 돌려준다.
                    BigDecimal raw = (BigDecimal) rs.getObject("similarity");
                    Double similarity = raw == null ? null : raw.doubleValue();
                    return new SearchHit(
                            rs.getLong("document_id"), rs.getInt("document_version"), rs.getInt("chunk_no"),
                            rs.getString("content"), rs.getString("section_path"),
                            rs.getString("chunk_tag"), similarity == null ? 0 : similarity, similarity);
                })
                .list();
    }

    public record Turn(String role, String content, Instant createdAt) {
    }

    /** 질문 재작성과 요약에 쓸 최근 대화. */
    @Transactional(readOnly = true)
    public List<Turn> recentTurns(long conversationId, int limit) {
        List<Turn> turns = jdbc.sql("""
                        SELECT role::text AS role, content, created_at
                        FROM chat.message
                        WHERE conversation_id = :id
                        ORDER BY created_at DESC, id DESC
                        LIMIT :limit
                        """)
                .param("id", conversationId).param("limit", limit)
                .query((rs, i) -> new Turn(rs.getString("role"), rs.getString("content"),
                        rs.getTimestamp("created_at").toInstant()))
                .list();
        return turns.reversed();
    }

    @Transactional(readOnly = true)
    public int turnCount(long conversationId) {
        return jdbc.sql("SELECT count(*) FROM chat.message WHERE conversation_id = :id")
                .param("id", conversationId).query(Integer.class).single();
    }
}

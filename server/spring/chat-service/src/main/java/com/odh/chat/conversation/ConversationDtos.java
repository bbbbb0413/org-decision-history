package com.odh.chat.conversation;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import com.fasterxml.jackson.databind.JsonNode;

import java.time.Instant;
import java.util.List;

/** 대화 관련 요청·응답 타입. */
public final class ConversationDtos {

    public record Conversation(Long id, String title, Instant createdAt, Instant updatedAt) {
    }

    public record Message(
            Long id,
            Long conversationId,
            String role,
            String content,
            String modelName,
            String answerStatus,
            Integer latencyMs,
            int sourceCount,
            Instant createdAt
    ) {
    }

    public record Source(
            Long documentId,
            String documentTitle,
            String documentType,
            String authoredAt,
            Integer documentVersion,
            Integer chunkNo,
            String sectionPath,
            String chunkTag,
            Double similarity,
            Integer rank,
            Long fileId,
            // 바운딩 박스. PDF가 아니면 셋 다 null
            Integer pageStart,
            Integer pageEnd,
            JsonNode bboxes
    ) {
    }

    /**
     * 질문 요청.
     *
     * <p>제약이 없으면 빈 질문도 파이프라인을 돌고, 아주 긴 질문이 그대로 저장돼 그 대화의
     * 이후 프롬프트를 모델 한도 밖으로 밀어낸다(Groq 413).
     * 길이 상한은 프롬프트의 [최근 대화] 예산(prompt.py RECENT_TURNS_BUDGET)과 함께
     * 두 겹으로 막는다 — 여기서 막지 못한 과거 메시지는 그쪽이 잘라 낸다.
     */
    public record AskRequest(
            @NotBlank(message = "질문을 입력해 주세요")
            @Size(max = 2000, message = "질문은 2000자를 넘을 수 없습니다")
            String content) {
    }

    /**
     * 답변 피드백.
     *
     * <p>rating 은 chat.feedback_rating enum 으로 캐스팅되므로, 검증 없이 통과시키면
     * Postgres 캐스트 오류가 500 으로 새어 나간다. 허용 값을 여기서 고정한다.
     */
    public record FeedbackRequest(
            @Pattern(regexp = "HELPFUL|NOT_HELPFUL", message = "rating 은 HELPFUL 또는 NOT_HELPFUL 이어야 합니다")
            @NotBlank(message = "rating 은 필수입니다")
            String rating,
            @Size(max = 1000, message = "사유는 1000자를 넘을 수 없습니다")
            String reason) {
    }

    private ConversationDtos() {
    }
}

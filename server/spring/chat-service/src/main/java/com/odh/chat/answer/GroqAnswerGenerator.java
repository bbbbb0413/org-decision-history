package com.odh.chat.answer;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

/**
 * Groq 논스트리밍 호출.
 *
 * 답변 스트리밍 생성은 rag-service(Python/LangGraph)가 한다.
 * 이 클래스는 {@link com.odh.chat.timeline.ConflictDetector}가 쓰는
 * 경량 모델 단발 호출이다.
 */
public class GroqAnswerGenerator implements AnswerGenerator {

    private static final Logger log = LoggerFactory.getLogger(GroqAnswerGenerator.class);
    private static final String PATH = "/openai/v1/chat/completions";

    private final RestClient client;
    private final LlmProperties properties;

    public GroqAnswerGenerator(RestClient.Builder builder, LlmProperties properties) {
        this.client = builder.baseUrl(properties.endpoint()).build();
        this.properties = properties;
    }

    @Override
    public String complete(String systemPrompt, String userPrompt, int maxTokens) {
        try {
            @SuppressWarnings("unchecked")
            Map<String, Object> body = client.post()
                    .uri(PATH)
                    .header("Authorization", "Bearer " + properties.apiKey())
                    .body(Map.of(
                            "model", properties.fallbackModel(),
                            "messages", List.of(
                                    Map.of("role", "system", "content", systemPrompt),
                                    Map.of("role", "user", "content", userPrompt)),
                            "max_tokens", maxTokens,
                            "temperature", 0.2))
                    .retrieve().body(Map.class);
            return firstMessageContent(body);
        } catch (Exception e) {
            // 재작성과 요약은 실패해도 원문으로 진행할 수 있다. 답변 자체를 막지 않는다.
            log.warn("경량 모델 호출 실패. 원문을 그대로 쓴다. 사유={}", e.toString());
            return userPrompt;
        }
    }

    @SuppressWarnings("unchecked")
    private String firstMessageContent(Map<String, Object> body) {
        List<Map<String, Object>> choices = (List<Map<String, Object>>) body.get("choices");
        Map<String, Object> message = (Map<String, Object>) choices.get(0).get("message");
        return String.valueOf(message.get("content"));
    }
}

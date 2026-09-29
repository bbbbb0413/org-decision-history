package com.odh.chat.answer;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param apiKey       Groq API 키. 비어 있으면 근거 발췌 방식으로 동작한다
 * @param endpoint     Groq API 주소
 * @param primaryModel 기본 답변 모델
 * @param fallbackModel 한도 초과 시 쓰는 경량 모델
 * @param maxOutputTokens 답변 여유 토큰
 */
@ConfigurationProperties(prefix = "llm")
public record LlmProperties(
        String apiKey,
        String endpoint,
        String primaryModel,
        String fallbackModel,
        int maxOutputTokens
) {
    public LlmProperties {
        if (endpoint == null || endpoint.isBlank()) endpoint = "https://api.groq.com";
        if (primaryModel == null || primaryModel.isBlank()) primaryModel = "llama-3.3-70b-versatile";
        if (fallbackModel == null || fallbackModel.isBlank()) fallbackModel = "llama-3.1-8b-instant";
        if (maxOutputTokens <= 0) maxOutputTokens = 1500;
    }

    public boolean hasApiKey() {
        return apiKey != null && !apiKey.isBlank();
    }
}

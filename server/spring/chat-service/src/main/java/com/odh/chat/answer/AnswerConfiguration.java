package com.odh.chat.answer;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

@Configuration
@EnableConfigurationProperties(LlmProperties.class)
public class AnswerConfiguration {

    /**
     * API 키가 있으면 Groq 를, 없으면 대체 구현을 쓴다.
     * 키가 없다고 상충 판정 전체를 막으면 나머지 단계를 확인할 수 없기 때문이다.
     */
    @Bean
    public AnswerGenerator answerGenerator(RestClient.Builder builder, LlmProperties properties) {
        return properties.hasApiKey()
                ? new GroqAnswerGenerator(builder, properties)
                : new ExtractiveAnswerGenerator();
    }
}

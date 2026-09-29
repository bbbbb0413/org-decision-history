package com.odh.chat.answer;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * API 키가 없을 때 쓰는 대체 구현.
 *
 * 답변 스트리밍 생성은 rag-service(Python/LangGraph)가 한다.
 * 이 클래스는 {@link com.odh.chat.timeline.ConflictDetector}가 쓰는
 * 경량 호출 자리이며, LLM 없이는 의미가 없어 원문을 그대로 돌려준다.
 *
 * llm.api-key 를 넣으면 GroqAnswerGenerator 로 바뀐다.
 */
public class ExtractiveAnswerGenerator implements AnswerGenerator {

    private static final Logger log = LoggerFactory.getLogger(ExtractiveAnswerGenerator.class);

    public ExtractiveAnswerGenerator() {
        log.warn("llm.api-key 가 비어 있어 상충 판정 비교를 건너뛴다.");
    }

    @Override
    public String complete(String systemPrompt, String userPrompt, int maxTokens) {
        return userPrompt;
    }
}

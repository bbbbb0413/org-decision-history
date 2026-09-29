package com.odh.chat.answer;

/**
 * 경량 LLM 호출. 타임라인의 상충 판정에 쓴다.
 *
 * 답변 스트리밍 생성은 rag-service(Python/LangGraph)가 한다.
 * 여기 남은 건 {@link com.odh.chat.timeline.ConflictDetector}가 쓰는
 * 논스트리밍 단발 호출뿐이다.
 */
public interface AnswerGenerator {

    /** 대화 요약과 질문 재작성에 쓰던 경량 호출. 지금은 상충 판정 문구 비교에 쓴다. */
    String complete(String systemPrompt, String userPrompt, int maxTokens);
}

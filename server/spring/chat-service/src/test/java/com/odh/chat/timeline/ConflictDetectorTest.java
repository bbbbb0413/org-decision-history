package com.odh.chat.timeline;

import com.odh.chat.answer.AnswerGenerator;
import com.odh.chat.answer.LlmProperties;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 문서 사이 상충 판정.
 *
 * 결정 상태 1차 판정과, 상태로 못 가릴 때만 LLM 을 부르는 2차 판정의 경계를 확인한다.
 * LLM 호출 여부는 StubAnswerGenerator 가 호출 횟수를 세어 검증한다 — 상태만으로 가려지는
 * 쌍에서 불필요하게 LLM 을 부르면 비용이 든다.
 */
class ConflictDetectorTest {

    @Test
    void singleCandidate_hasNoConflicts() {
        ConflictDetector detector = detector(new LlmProperties(null, null, null, null, 0), null);

        var result = detector.detect(List.of(source(1, "CURRENT", "결정 A")));

        assertThat(result).isEmpty();
    }

    @Test
    void pairMissingDecisionExcerpt_isSkipped() {
        StubAnswerGenerator stub = new StubAnswerGenerator(null);
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "CURRENT", null),
                source(2, "DEPRECATED", "결정 B")));

        assertThat(result).isEmpty();
        assertThat(stub.callCount).isZero();
    }

    @Test
    void deprecatedVsCurrent_isConflictWithoutCallingLlm() {
        StubAnswerGenerator stub = new StubAnswerGenerator(null);
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "DEPRECATED", "옛 결정"),
                source(2, "CURRENT", "새 결정")));

        assertThat(result).hasSize(1);
        assertThat(result.get(0).documentAId()).isEqualTo(1L);
        assertThat(result.get(0).documentBId()).isEqualTo(2L);
        assertThat(result.get(0).difference()).contains("폐기").contains("현재 적용");
        assertThat(stub.callCount).isZero();
    }

    @Test
    void changedVsCurrent_isConflictWithoutCallingLlm() {
        StubAnswerGenerator stub = new StubAnswerGenerator(null);
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "CURRENT", "새 결정"),
                source(2, "CHANGED", "옛 결정")));

        assertThat(result).hasSize(1);
        assertThat(result.get(0).difference()).contains("변경").contains("현재 적용");
        assertThat(stub.callCount).isZero();
    }

    @Test
    void bothCurrent_isConflictWithoutCallingLlm() {
        StubAnswerGenerator stub = new StubAnswerGenerator(null);
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "CURRENT", "결정 A"),
                source(2, "CURRENT", "결정 B")));

        assertThat(result).hasSize(1);
        assertThat(result.get(0).difference()).contains("모두 현재 적용");
        assertThat(stub.callCount).isZero();
    }

    @Test
    void neitherSettled_withoutApiKey_stopsAtFirstPassWithNoConflict() {
        StubAnswerGenerator stub = new StubAnswerGenerator(null);
        ConflictDetector detector = detector(new LlmProperties(null, null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "DRAFT", "결정 A"),
                source(2, "DRAFT", "결정 B")));

        assertThat(result).isEmpty();
        assertThat(stub.callCount).isZero();
    }

    @Test
    void neitherSettled_withApiKey_llmSaysNone_yieldsNoConflict() {
        StubAnswerGenerator stub = new StubAnswerGenerator("없음");
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "DRAFT", "결정 A"),
                source(2, "DRAFT", "결정 B")));

        assertThat(result).isEmpty();
        assertThat(stub.callCount).isEqualTo(1);
    }

    @Test
    void neitherSettled_withApiKey_llmFindsDifference_yieldsConflict() {
        StubAnswerGenerator stub = new StubAnswerGenerator("예산 한도가 500만원에서 800만원으로 바뀌었다.");
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "DRAFT", "결정 A"),
                source(2, "DRAFT", "결정 B")));

        assertThat(result).hasSize(1);
        assertThat(result.get(0).difference()).isEqualTo("예산 한도가 500만원에서 800만원으로 바뀌었다.");
    }

    @Test
    void llmVerdict_longerThanLimit_isClipped() {
        String longVerdict = "차".repeat(600);
        StubAnswerGenerator stub = new StubAnswerGenerator(longVerdict);
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "DRAFT", "결정 A"),
                source(2, "DRAFT", "결정 B")));

        assertThat(result.get(0).difference()).hasSize(500);
        assertThat(result.get(0).difference()).endsWith("...");
    }

    @Test
    void llmThrows_isTreatedAsNoConflict() {
        StubAnswerGenerator stub = StubAnswerGenerator.throwing(new RuntimeException("timeout"));
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        var result = detector.detect(List.of(
                source(1, "DRAFT", "결정 A"),
                source(2, "DRAFT", "결정 B")));

        assertThat(result).isEmpty();
    }

    @Test
    void threeCandidates_comparesEveryPairOnce() {
        StubAnswerGenerator stub = new StubAnswerGenerator("없음");
        ConflictDetector detector = detector(new LlmProperties("key", null, null, null, 0), stub);

        // 셋 다 상태로는 안 가려지는 조합 (DRAFT/DRAFT/DRAFT) 이라 3쌍(1-2, 1-3, 2-3) 모두 LLM 을 탄다
        var result = detector.detect(List.of(
                source(1, "DRAFT", "결정 A"),
                source(2, "DRAFT", "결정 B"),
                source(3, "DRAFT", "결정 C")));

        assertThat(result).isEmpty();
        assertThat(stub.callCount).isEqualTo(3);
    }

    private ConflictDetector detector(LlmProperties props, AnswerGenerator generator) {
        return new ConflictDetector(generator, props);
    }

    private TimelineSource source(long documentId, String status, String decisionExcerpt) {
        return new TimelineSource(documentId, "문서" + documentId, "DECISION", "2024-01-0" + documentId,
                status, "프로젝트", decisionExcerpt, null);
    }

    /** LLM 호출을 흉내내며 호출 횟수를 센다. 고정 응답을 돌려주거나, 예외를 흉내낸다. */
    private static class StubAnswerGenerator implements AnswerGenerator {
        private final String fixedResponse;
        private final RuntimeException toThrow;
        int callCount = 0;

        private StubAnswerGenerator(String fixedResponse, RuntimeException toThrow) {
            this.fixedResponse = fixedResponse;
            this.toThrow = toThrow;
        }

        StubAnswerGenerator(String fixedResponse) {
            this(fixedResponse, null);
        }

        static StubAnswerGenerator throwing(RuntimeException toThrow) {
            return new StubAnswerGenerator(null, toThrow);
        }

        @Override
        public String complete(String systemPrompt, String userPrompt, int maxTokens) {
            callCount++;
            if (toThrow != null) throw toThrow;
            return fixedResponse;
        }
    }
}

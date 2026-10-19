package com.odh.chat.timeline;

import com.odh.chat.answer.AnswerGenerator;
import com.odh.chat.answer.LlmProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * 문서 사이의 상충을 찾는다.
 *
 * 결정 상태와 청크 유형 태그를 먼저 쓴다. 사람이 손으로 넣은 값이라 완전하지 않지만
 * 비용이 들지 않고 판정 근거가 눈에 보인다.
 * 상태만으로 가릴 수 없을 때만 LLM 비교를 부른다.
 *
 * 어느 쪽이 옳은지는 판정하지 않는다. 문서 쌍과 차이만 제시하고 판단은 사용자가 한다.
 * 잘못된 판정이 사용자의 결론이 되지 않게 하려는 것이다.
 */
@Component
public class ConflictDetector {

    private static final Logger log = LoggerFactory.getLogger(ConflictDetector.class);

    private static final String COMPARE_SYSTEM = """
            너는 두 문서의 결정 내용을 비교하는 도구다.
            두 글이 같은 사안에 대해 서로 다른 결정을 말하는지 판정한다.
            서로 다르면 무엇이 어떻게 다른지 한 문장으로 쓴다.
            같거나 비교할 수 없으면 정확히 "없음" 이라고만 쓴다.
            어느 쪽이 옳은지는 말하지 않는다.
            """;

    private final AnswerGenerator generator;
    private final LlmProperties llmProperties;
    private final int maxDifferenceLength;

    public ConflictDetector(AnswerGenerator generator, LlmProperties llmProperties, TimelineProperties timeline) {
        this.generator = generator;
        this.llmProperties = llmProperties;
        this.maxDifferenceLength = timeline.maxDifferenceLength();
    }

    /**
     * @param candidates 같은 주제로 묶인 문서들. 작성일 오름차순
     */
    public List<TimelineDtos.Conflict> detect(List<TimelineSource> candidates) {
        List<TimelineDtos.Conflict> conflicts = new ArrayList<>();

        for (int i = 0; i < candidates.size(); i++) {
            for (int j = i + 1; j < candidates.size(); j++) {
                TimelineSource a = candidates.get(i);
                TimelineSource b = candidates.get(j);

                // 결정 대목이 없는 문서끼리는 상충을 따질 근거가 없다
                if (a.decisionExcerpt() == null || b.decisionExcerpt() == null) {
                    continue;
                }
                String difference = compare(a, b);
                if (difference == null) {
                    continue;
                }
                conflicts.add(new TimelineDtos.Conflict(
                        a.documentId(), a.title(), a.authoredAt(), a.decisionStatus(),
                        b.documentId(), b.title(), b.authoredAt(), b.decisionStatus(),
                        difference));
            }
        }
        return conflicts;
    }

    /**
     * @return 상충 설명. 상충이 아니면 null
     */
    private String compare(TimelineSource a, TimelineSource b) {
        // 1차: 결정 상태로 가린다. 하나가 폐기·변경이고 다른 하나가 현재 적용이면
        //      같은 사안의 결정이 바뀐 것이다.
        String byStatus = compareByStatus(a, b);
        if (byStatus != null) {
            return byStatus;
        }

        // 2차: 상태로 가릴 수 없으면 내용을 비교한다. LLM 이 없으면 여기서 멈춘다.
        if (!llmProperties.hasApiKey()) {
            return null;
        }
        try {
            String prompt = """
                    [문서 A] %s (%s, %s)
                    %s

                    [문서 B] %s (%s, %s)
                    %s
                    """.formatted(
                    a.title(), a.authoredAt(), statusLabel(a.decisionStatus()), a.decisionExcerpt(),
                    b.title(), b.authoredAt(), statusLabel(b.decisionStatus()), b.decisionExcerpt());

            String verdict = generator.complete(COMPARE_SYSTEM, prompt, 200);
            if (verdict == null || verdict.isBlank() || verdict.strip().startsWith("없음")) {
                return null;
            }
            return clip(verdict.strip());
        } catch (Exception e) {
            // 판정에 실패하면 상충이 없는 것으로 둔다.
            // 근거 없이 상충이라고 표시하면 없는 문제를 만든다.
            log.warn("상충 비교 실패 documentA={} documentB={} 사유={}",
                    a.documentId(), b.documentId(), e.toString());
            return null;
        }
    }

    private String compareByStatus(TimelineSource a, TimelineSource b) {
        boolean aSettled = "CURRENT".equals(a.decisionStatus());
        boolean bSettled = "CURRENT".equals(b.decisionStatus());
        boolean aStale = "DEPRECATED".equals(a.decisionStatus()) || "CHANGED".equals(a.decisionStatus());
        boolean bStale = "DEPRECATED".equals(b.decisionStatus()) || "CHANGED".equals(b.decisionStatus());

        if (aStale && bSettled) {
            return "%s 의 결정은 %s 상태이고, %s 의 결정이 현재 적용 중이다."
                    .formatted(a.title(), statusLabel(a.decisionStatus()), b.title());
        }
        if (aSettled && bStale) {
            return "%s 의 결정은 %s 상태이고, %s 의 결정이 현재 적용 중이다."
                    .formatted(b.title(), statusLabel(b.decisionStatus()), a.title());
        }
        // 둘 다 현재 적용인데 결정 대목이 있으면, 같은 사안에 두 결정이 살아 있는 것이다
        if (aSettled && bSettled) {
            return "두 문서 모두 현재 적용 상태로 결정을 담고 있다. 어느 쪽이 유효한지 확인이 필요하다.";
        }
        return null;
    }

    private String statusLabel(String status) {
        return switch (status == null ? "" : status) {
            case "CURRENT" -> "현재 적용";
            case "CHANGED" -> "변경";
            case "DEPRECATED" -> "폐기";
            default -> "미확정";
        };
    }

    private String clip(String text) {
        return text.length() > maxDifferenceLength
                ? text.substring(0, maxDifferenceLength - 3) + "..."
                : text;
    }
}

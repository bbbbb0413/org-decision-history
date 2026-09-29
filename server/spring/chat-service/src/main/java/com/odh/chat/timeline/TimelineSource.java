package com.odh.chat.timeline;

/**
 * 타임라인 한 사건의 원천 정보.
 *
 * @param decisionExcerpt 결정사항으로 태깅된 청크의 앞부분.
 *                        상충 판정과 화면 표시에 함께 쓴다. 없으면 null
 */
public record TimelineSource(
        long documentId,
        String title,
        String documentType,
        String authoredAt,
        String decisionStatus,
        String projectName,
        String decisionExcerpt,
        Long fileId
) {
}

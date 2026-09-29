package com.odh.chat.search;

/**
 * 검색 결과 한 건.
 *
 * @param documentId       문서 식별자
 * @param documentVersion  색인 당시 버전
 * @param chunkNo          청크 번호. 원문 위치 이동에 쓴다
 * @param content          청크 본문. 답변 프롬프트의 근거가 된다
 * @param sectionPath      헤딩 경로
 * @param chunkTag         결정사항·논의·액션아이템·배경
 * @param score            병합 점수. 절대값에 의미가 없고 순위만 뜻한다 (RRF 결과에만 붙는다)
 * @param vectorSimilarity 벡터 검색에서 나온 실제 코사인 유사도(0~1). 전문검색으로만 걸린 청크는 null —
 *                         두 검색은 점수 단위가 달라 하나로 합칠 수 없다
 */
public record SearchHit(
        long documentId,
        int documentVersion,
        int chunkNo,
        String content,
        String sectionPath,
        String chunkTag,
        double score,
        Double vectorSimilarity
) {
    /** 같은 청크인지 판정한다. 두 검색 결과를 병합할 때 쓴다. */
    public String key() {
        return documentId + ":" + documentVersion + ":" + chunkNo;
    }
}

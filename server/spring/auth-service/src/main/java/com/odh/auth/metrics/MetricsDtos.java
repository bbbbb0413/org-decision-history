package com.odh.auth.metrics;

import java.time.Instant;
import java.util.List;

/**
 * 제품 지표.
 *
 * <p>핵심 지표 8개 중 6개(1·2·3·4·7·8)를 담는다. 나머지 둘은 여기 없다.
 * 5번(색인 반영 지연)은 운영 현황의 {@code indexLagSeconds} 가 이미 보여주고,
 * 6번(온보딩 탐색 시간)은 원천 데이터 자체가 없다 — 측정 방법을 온보딩 기능과 함께
 * 설계해야 한다.
 *
 * <p>비율은 서버에서 나누지 않고 분자·분모를 그대로 내려준다. 표본이 3건일 때
 * "67%"만 보이면 판단을 오히려 해치기 때문이다. 화면이 분모를 함께 보여준다.
 */
public final class MetricsDtos {

    /**
     * 답변 품질 (핵심 지표 1: 출처 포함 답변 성공률, 목표 85%).
     *
     * <p>성공의 정의를 {@code okWithSources / assistantMessages} 로 잡았다 — 상태가 OK 이고
     * 인용한 출처가 1건 이상인 답변이다. 명세는 "출처 포함 답변 성공률"이라고만 적어
     * 정의가 열려 있었다. {@code NO_EVIDENCE}(근거를 못 찾아 그렇다고 답한 경우)는 분자에서
     * 빠지지만 그게 제품 결함이라는 뜻은 아니다 — 그래서 {@code byStatus} 로 분해를 함께 준다.
     *
     * @param withSources   상태와 무관하게 출처가 1건 이상인 답변 수
     * @param ok            출처 유무와 무관하게 상태가 OK 인 답변 수
     * @param okWithSources 지표 1의 분자
     */
    public record AnswerQuality(long assistantMessages, long withSources, long ok, long okWithSources,
                                List<StatusCount> byStatus) {
        static AnswerQuality empty() {
            return new AnswerQuality(0, 0, 0, 0, List.of());
        }
    }

    /** {@code answer_status} 별 답변 수. 값이 없는 답변은 {@code UNKNOWN} 으로 모은다. */
    public record StatusCount(String status, long count) {
    }

    /**
     * 출처 검증 가능성 (핵심 지표 2: 사용자 검증 가능 비율, 목표 95%).
     *
     * <p>"검증 가능"을 <b>원본 파일을 내려받을 수 있는가</b>로 정의했다. 화면이 실제로 주는
     * 수단이 그것이기 때문이다 — {@code SourcePanel} 의 "원본 받기" 버튼은 {@code fileId} 가
     * 있을 때만 뜬다.
     *
     * <p>{@code withCoordinates} 는 보조 지표다. 좌표 하이라이트는 PDF 청크만 가진다
     * (index-worker {@code V3__chunk_bbox.sql}: "PDF만 채운다"). 그래서 이 값이 낮은 것은
     * 결함이 아니라 문서 형식 구성을 뜻한다 — 지표 2의 판정에 쓰면 안 된다.
     *
     * @param citedSources    기간 내 답변이 인용한 출처 수
     * @param withOriginalFile 그중 해당 버전의 원본 파일이 있는 수 (지표 2의 분자)
     * @param withCoordinates  그중 좌표까지 있는 수 (보조)
     */
    public record SourceVerifiability(long citedSources, long withOriginalFile, long withCoordinates) {
        static SourceVerifiability empty() {
            return new SourceVerifiability(0, 0, 0);
        }
    }

    /**
     * 답변 피드백 (핵심 지표 3: 유용 평가 비율, 목표 80%).
     *
     * <p>{@code responded / assistantMessages}(응답률)를 반드시 함께 본다. 유용 비율만 보면
     * 표본 3건에 2건이 HELPFUL 인 상태를 "67%"로 읽게 된다.
     *
     * <p>이 지표를 만들면서 {@code answer_feedback} 이 처음으로 읽히게 됐다. 그전까지
     * 이 테이블은 INSERT 만 있었다 — 사용자에게 평가를 받아 두고 아무도 보지 않았다.
     */
    public record Feedback(long responded, long helpful, long notHelpful, long assistantMessages) {
        static Feedback empty() {
            return new Feedback(0, 0, 0, 0);
        }
    }

    /**
     * 응답 시간 (핵심 지표 4: 일반 질문 5초 이내).
     *
     * <p>측정 대상은 {@code chat.message.latency_ms} 가 있는 답변이다. <b>복합 타임라인
     * 질문(목표 10초)은 여기 없다</b> — 타임라인은 {@code GET /api/timeline} 이라 메시지로
     * 남지 않아 이 경로로는 측정할 수 없다.
     *
     * @param measured       latency 가 기록된 답변 수
     * @param averageMs      평균. 표본이 없으면 null
     * @param p95Ms          95 분위. 표본이 없으면 null
     * @param overTargetCount 목표({@code targetMs})를 넘긴 답변 수
     */
    public record Latency(long measured, Long averageMs, Long p95Ms, long overTargetCount, long targetMs) {
        static Latency empty(long targetMs) {
            return new Latency(0, null, null, 0, targetMs);
        }
    }

    /**
     * 권한 차단 (핵심 지표 7 관련).
     *
     * <p><b>이 값은 "노출 건수"가 아니다.</b> 명세의 지표 7은 "권한 위반으로 인한 문서 노출
     * 건수 0건"인데, 노출은 일어나지 않았음을 로그로 증명할 수 없다 — 감사 로그에 남는
     * {@code DENIED} 는 오히려 <b>차단이 동작했다</b>는 기록이다. 그래서 이름을 차단으로
     * 바꿔 달고, 선행 지표로만 쓴다. 갑자기 치솟으면 권한 설정이나 링크 공유를 들여다볼
     * 신호다.
     */
    public record AccessDenials(long total, List<ActionCount> byAction) {
        static AccessDenials empty() {
            return new AccessDenials(0, List.of());
        }
    }

    /** 감사 액션별 차단 수. */
    public record ActionCount(String action, long count) {
    }

    /**
     * 사용 정착도 (핵심 지표 8: 활성 사용자 중 질문한 비율, 목표 60%).
     *
     * @param activeUsers 상태가 ACTIVE 인 계정 수 (분모)
     * @param askedUsers  기간 내 질문을 한 사람 수 (분자)
     * @param questions   기간 내 질문 수. 1인당 질문 수를 가늠하려고 함께 준다
     */
    public record Adoption(long activeUsers, long askedUsers, long questions) {
        static Adoption empty() {
            return new Adoption(0, 0, 0);
        }
    }

    /**
     * 지표 묶음 하나.
     *
     * @param degraded 일부 구획을 읽지 못해 0으로 내려간 항목의 이름. 비어 있으면 전부 정상이다.
     *                 조회 실패를 "0건"으로 보여주면 지표를 잘못 읽게 되므로 감추지 않는다.
     */
    public record ProductMetrics(Instant from, Instant to, int days,
                                 AnswerQuality answerQuality,
                                 SourceVerifiability sources,
                                 Feedback feedback,
                                 Latency latency,
                                 AccessDenials denials,
                                 Adoption adoption,
                                 List<String> degraded) {
    }

    /**
     * 피드백 목록의 한 줄.
     *
     * <p>지표 3을 숫자로만 보여주면 "78%네"에서 끝난다. 부정 평가가 달린 답변의 실제 질문과
     * 답변까지 가 볼 수 있어야 고칠 수 있다.
     *
     * @param question      그 답변 직전의 사용자 질문. 없으면 null
     * @param answerPreview 답변 앞부분만 자른 것. 전문은 상세 조회에서 준다
     */
    public record FeedbackItem(long messageId, long conversationId, String rating, String reason,
                               Instant createdAt, String raterName, String question,
                               String answerPreview, String answerStatus, Integer latencyMs,
                               long sourceCount) {
    }

    /** 피드백 상세. 질문·재작성 질문·답변 전문·인용한 출처까지 한 번에 본다. */
    public record FeedbackDetail(long messageId, long conversationId, String rating, String reason,
                                 Instant createdAt, String raterName, String question,
                                 String rewrittenQuery, String answer, String answerStatus,
                                 String modelName, Integer latencyMs, Instant answeredAt,
                                 List<CitedSource> sources) {
    }

    /**
     * 답변이 인용한 출처 하나.
     *
     * @param hasOriginalFile 원본 파일을 내려받을 수 있는지. 지표 2와 같은 기준이다
     * @param similarity      전문검색으로만 걸린 근거는 null 이다
     */
    public record CitedSource(long documentId, String documentTitle, int documentVersion, int chunkNo,
                              int rank, Double similarity, String sectionPath, boolean hasOriginalFile) {
    }

    private MetricsDtos() {
    }
}

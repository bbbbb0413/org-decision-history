package com.odh.auth.metrics;

import com.odh.auth.domain.SystemRole;
import com.odh.auth.policy.PolicyService;
import com.odh.auth.repository.UserRoleRepository;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import com.odh.common.InternalAuth;
import com.odh.common.web.PageResponse;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 제품 지표. 시스템 관리자만 조회한다.
 *
 * <p>조직·팀 관리자에게도 열지 않았다. 피드백 드릴다운이 <b>전 조직 사용자의 실제 질문과
 * 답변 전문</b>을 보여주기 때문이다. 조직 범위로 좁혀 여는 것은 집계 자체에 조직 필터를
 * 넣은 뒤에 할 일이고, 그건 후속으로 미뤘다.
 */
@RestController
@RequestMapping("/api/admin/metrics")
public class ProductMetricsController {

    private final ProductMetricsService metrics;
    private final UserRoleRepository roles;

    public ProductMetricsController(ProductMetricsService metrics, UserRoleRepository roles) {
        this.metrics = metrics;
        this.roles = roles;
    }

    /** 기간 요약. {@code days} 는 1~365 로 보정된다(기본 30). */
    @GetMapping
    public MetricsDtos.ProductMetrics metrics(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @RequestParam(defaultValue = "30") int days) {
        requireSystemAdmin(actorId);
        return metrics.metrics(days);
    }

    /**
     * 평가가 달린 답변 목록. {@code rating} 은 {@code HELPFUL}/{@code NOT_HELPFUL},
     * 그 밖의 값이면 필터 없이 전체다.
     */
    @GetMapping("/feedback")
    public PageResponse<MetricsDtos.FeedbackItem> feedback(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @RequestParam(required = false) String rating,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        requireSystemAdmin(actorId);
        return metrics.feedbackItems(rating, page, size);
    }

    /** 평가가 달린 답변 하나의 질문·답변 전문과 인용 출처. */
    @GetMapping("/feedback/{messageId}")
    public ResponseEntity<?> feedbackDetail(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable long messageId) {
        requireSystemAdmin(actorId);
        return metrics.feedbackDetail(messageId)
                .<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                        ErrorCode.NOT_FOUND, "해당 답변의 평가를 찾을 수 없습니다.", null)));
    }

    /** 운영 현황과 같은 기준이다. 전 조직의 질문·답변을 노출하므로 조직 관리자에게도 열지 않는다. */
    private void requireSystemAdmin(Long actorId) {
        boolean systemAdmin = roles.findByUserId(actorId).stream()
                .anyMatch(r -> r.getRole() == SystemRole.SYSTEM_ADMIN);
        if (!systemAdmin) {
            throw new PolicyService.ScopeViolationException();
        }
    }
}

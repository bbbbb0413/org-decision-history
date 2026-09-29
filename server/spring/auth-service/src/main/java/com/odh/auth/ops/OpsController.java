package com.odh.auth.ops;

import com.odh.auth.domain.SystemRole;
import com.odh.auth.policy.PolicyService;
import com.odh.auth.repository.UserRoleRepository;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import com.odh.common.InternalAuth;
import com.odh.common.web.PageResponse;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/** 운영 현황. 시스템 관리자만 조회한다. */
@RestController
@RequestMapping("/api/admin/ops")
public class OpsController {

    private final OpsHealthService health;
    private final OpsIndexService index;
    private final OpsDlqService dlq;
    private final UserRoleRepository roles;

    public OpsController(OpsHealthService health, OpsIndexService index, OpsDlqService dlq,
                         UserRoleRepository roles) {
        this.health = health;
        this.index = index;
        this.dlq = dlq;
        this.roles = roles;
    }

    @GetMapping("/health")
    public OpsDtos.OpsHealth health(@RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId) {
        requireSystemAdmin(actorId);
        return health.check();
    }

    @GetMapping("/index-status")
    public OpsDtos.OpsIndexStatus indexStatus(@RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId) {
        requireSystemAdmin(actorId);
        return index.indexStatus();
    }

    @GetMapping("/errors")
    public PageResponse<OpsDtos.OpsError> errors(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @RequestParam(required = false) String service,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        requireSystemAdmin(actorId);
        return index.errors(service, page, size);
    }

    @GetMapping("/trace/{correlationId}")
    public ResponseEntity<?> trace(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable String correlationId) {
        requireSystemAdmin(actorId);
        List<OpsDtos.TraceSpan> spans = index.trace(correlationId);
        if (spans.isEmpty()) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorResponse.of(
                    ErrorCode.NOT_FOUND, "해당 상관관계 ID 의 추적 정보를 찾을 수 없습니다.", null));
        }
        return ResponseEntity.ok(spans);
    }

    // ---- 스트림 DLQ ----

    /** 스트림별 DLQ 건수·가장 오래된 항목 시각. */
    @GetMapping("/dlq")
    public List<OpsDtos.DlqStatus> dlqStatuses(@RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId) {
        requireSystemAdmin(actorId);
        return dlq.statuses();
    }

    /** 최근 항목부터 조회한다. {@code streamKey} 는 허용 목록(`document-events`/`index-events`)에 있어야 한다. */
    @GetMapping("/dlq/{streamKey}/entries")
    public List<OpsDtos.DlqEntry> dlqEntries(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable String streamKey,
            @RequestParam(defaultValue = "20") int limit) {
        requireSystemAdmin(actorId);
        return dlq.entries(target(streamKey), limit);
    }

    /** 항목을 원본 스트림으로 되돌린다. 컨슈머가 다시 집어 간다. */
    @PostMapping("/dlq/{streamKey}/replay")
    public OpsDtos.DlqActionResult dlqReplay(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable String streamKey,
            @Valid @RequestBody OpsDtos.DlqActionRequest request) {
        requireSystemAdmin(actorId);
        return dlq.replay(target(streamKey), request.ids());
    }

    /** 되돌릴 수 없는 항목을 버린다. 되돌리지 않고 지우는 것이므로 그 이벤트는 영구히 유실된다. */
    @PostMapping("/dlq/{streamKey}/discard")
    public OpsDtos.DlqActionResult dlqDiscard(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable String streamKey,
            @Valid @RequestBody OpsDtos.DlqActionRequest request) {
        requireSystemAdmin(actorId);
        return dlq.discard(target(streamKey), request.ids());
    }

    private OpsDlqService.Target target(String streamKey) {
        return OpsDlqService.Target.ofKey(streamKey)
                .orElseThrow(() -> new UnknownStreamException(streamKey));
    }

    /** 허용 목록에 없는 스트림 키. 404 로 내려 임의 Redis 키 탐색에 쓰이지 않게 한다. */
    public static class UnknownStreamException extends RuntimeException {
        public UnknownStreamException(String streamKey) {
            super("알 수 없는 스트림입니다: " + streamKey);
        }
    }

    /** 운영 현황은 조직·팀 관리자에게도 열지 않는다. 전체 서비스·저장소를 노출하기 때문이다. */
    private void requireSystemAdmin(Long actorId) {
        boolean systemAdmin = roles.findByUserId(actorId).stream()
                .anyMatch(r -> r.getRole() == SystemRole.SYSTEM_ADMIN);
        if (!systemAdmin) {
            throw new PolicyService.ScopeViolationException();
        }
    }
}

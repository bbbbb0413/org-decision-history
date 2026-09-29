package com.odh.chat.timeline;

import com.odh.common.internal.PermissionScopeClient;
import com.odh.common.ErrorCode;
import com.odh.common.ErrorResponse;
import com.odh.common.InternalAuth;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class TimelineController {

    private final TimelineService timeline;
    private final PermissionScopeClient scopes;

    public TimelineController(TimelineService timeline, PermissionScopeClient scopes) {
        this.timeline = timeline;
        this.scopes = scopes;
    }

    @GetMapping("/api/timeline")
    public ResponseEntity<?> get(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                 @RequestParam String topic) {
        if (topic == null || topic.strip().length() < 2) {
            return ResponseEntity.badRequest().body(ErrorResponse.of(
                    ErrorCode.VALIDATION_FAILED, "주제는 두 글자 이상이어야 합니다.", null));
        }
        return ResponseEntity.ok(timeline.timelineOf(topic.strip(), scopes.scopeOf(userId)));
    }
}

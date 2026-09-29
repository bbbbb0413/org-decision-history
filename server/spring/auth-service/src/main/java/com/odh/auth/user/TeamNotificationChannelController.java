package com.odh.auth.user;

import com.odh.common.InternalAuth;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;

/** 팀별 거버넌스 알림 채널 설정. */
@RestController
public class TeamNotificationChannelController {

    private final TeamNotificationChannelService service;

    public TeamNotificationChannelController(TeamNotificationChannelService service) {
        this.service = service;
    }

    @GetMapping("/api/teams/{teamId}/notification-channel")
    public TeamNotificationChannelDtos.TeamNotificationChannel get(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long teamId) {
        return service.get(actorId, teamId);
    }

    @PutMapping("/api/teams/{teamId}/notification-channel")
    public TeamNotificationChannelDtos.TeamNotificationChannel update(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long actorId,
            @PathVariable Long teamId,
            @Valid @RequestBody TeamNotificationChannelDtos.UpdateRequest request) {
        return service.update(actorId, teamId, request);
    }
}

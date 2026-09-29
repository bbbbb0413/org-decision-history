package com.odh.auth.user;

import jakarta.validation.constraints.Email;
import org.hibernate.validator.constraints.URL;

import java.time.Instant;
import java.util.List;

/** 팀별 거버넌스 알림 채널 설정. */
public final class TeamNotificationChannelDtos {

    public record TeamNotificationChannel(
            Long teamId,
            String slackWebhookUrl,
            String teamsWebhookUrl,
            List<String> notificationEmails,
            Instant updatedAt
    ) {
    }

    /** 세 필드 모두 선택이다 — 팀은 원하는 채널만 설정한다. null 은 "미설정", 빈 문자열은 "해제"로 다룬다. */
    public record UpdateRequest(
            @URL String slackWebhookUrl,
            @URL String teamsWebhookUrl,
            List<@Email String> notificationEmails
    ) {
    }

    private TeamNotificationChannelDtos() {
    }
}

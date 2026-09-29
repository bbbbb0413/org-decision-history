-- 팀별 거버넌스 알림 채널 설정 (체크리스트 항목 9).
-- auth-service 가 이 테이블의 소유자다. 다른 서비스(governance-worker)는 읽기만 한다.
-- 모든 채널 컬럼이 nullable — 팀은 원하는 채널만 설정한다. 행이 아예 없으면 "미설정 팀"이고,
-- 이 경우 governance-worker 는 전역 설정 채널로 폴백한다.
CREATE TABLE team_notification_channel (
    team_id             BIGINT PRIMARY KEY REFERENCES team (id) ON DELETE CASCADE,
    slack_webhook_url   TEXT,
    teams_webhook_url   TEXT,
    -- 콤마 구분 이메일 목록. 비어 있으면 governance-worker 가 팀원 이메일(app_user.email)로 폴백한다.
    notification_email  TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

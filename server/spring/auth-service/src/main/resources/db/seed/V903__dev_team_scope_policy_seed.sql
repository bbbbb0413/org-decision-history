-- V901 은 팀 소속에게 "조직" 범위만 부여했다. 문서가 특정 팀으로 스코프되면
-- (document.team_id 가 채워지면 색인 scope_key 가 'org:{id}' 가 아니라 'team:{id}' 가 된다,
-- index-worker DocumentSource.scopeKey() 참고) 그 문서는 access_policy 에 scope_type='TEAM' 인
-- 허용 정책이 있어야만 보인다. 그런 정책이 하나도 없어서, 팀 범위로 등록된 문서는
-- SYSTEM_ADMIN 외에는 자기 팀 소속조차 못 보는 상태였다 — 팀 소속(user_team)이 자동으로
-- 그 팀 범위를 열어주지 않는다(기능 3.2, 정책은 명시적으로 등록해야 한다: REQ-FUNC-007).
-- 각 팀이 자기 자신의 팀 범위 문서를 읽을 수 있도록 기본 정책을 심어둔다.
INSERT INTO access_policy (subject_type, subject_team_id, scope_type, scope_team_id, can_read)
SELECT 'TEAM', t.id, 'TEAM', t.id, TRUE
FROM team t;

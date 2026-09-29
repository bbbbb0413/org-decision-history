-- 재발급 토큰 재사용 감지를 감사에 남기기 위한 액션 (구조점검 §5.1).
--
-- 회전을 넣으면서 "쓴 토큰을 다시 썼다"를 기록한다. 기존 액션 중 맞는 것이 없다 —
-- PASSWORD_CHANGE 나 ROLE_CHANGE 로 적으면 사실과 다르고, 이 사건은 그 사용자의 세션을
-- 전부 끊는 결과로 이어지므로 나중에 "왜 갑자기 로그아웃됐나"를 되짚을 때 필요하다.
--
-- result 는 DENIED 로 남긴다. 재사용 요청 자체가 거부된 것이고, 지표 7(권한 차단)에서도
-- 차단이 동작한 기록으로 세어지는 게 맞다.
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'TOKEN_REUSE_DETECTED';

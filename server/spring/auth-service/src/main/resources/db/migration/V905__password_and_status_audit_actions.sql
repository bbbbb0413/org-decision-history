-- 비밀번호 변경·재발급과 계정 활성/비활성 전환을 감사 로그에 남기기 위한 액션 추가
-- (2차 체크리스트 2·3번). 보안에 직결되는 조작이라 기존 ROLE_CHANGE 로 뭉뚱그리지 않고
-- 따로 구분한다.
--
-- 같은 파일 안에서 이 값을 바로 쓰지 않는다 — PostgreSQL 은 ALTER TYPE ... ADD VALUE 로 추가한
-- 값을 같은 트랜잭션 안에서 곧바로 참조하지 못한다(V902 와 같은 이유).
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'PASSWORD_CHANGE';
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'PASSWORD_RESET';
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'USER_STATUS_CHANGE';

-- 조직 생성·사용자 계정 발급 (기능 7.1 REQ-FUNC-033/034) 이력을 남기기 위한 감사 액션 추가.
-- 같은 파일 안에서 이 값을 바로 쓰지 않는다 — PostgreSQL 은 ALTER TYPE ... ADD VALUE 로 추가한
-- 값을 같은 트랜잭션 안에서 곧바로 참조하지 못한다.
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'ORGANIZATION_CREATED';
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'USER_CREATED';

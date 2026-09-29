-- 접근 정책·감사 로그 "조회" 거부를 감사에 남기기 위한 액션 (구조점검 §2.1).
--
-- 그동안 두 조회 엔드포인트에는 권한 검사가 아예 없어서 MEMBER 도 전체를 읽을 수 있었다.
-- 검사를 넣으면서 거부 기록도 남기는데, 기존 POLICY_CHANGE 로 적으면 "정책을 바꾸려다
-- 막혔다"로 읽혀 사실과 다르다. 조회 전용 액션을 따로 둔다.
--
-- 성공한 조회는 남기지 않는다 — 감사 로그를 읽을 때마다 감사 로그가 늘어나면
-- 목록이 자기 자신으로 차 버린다. 거부만 남긴다.
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'POLICY_VIEW';
ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'AUDIT_VIEW';

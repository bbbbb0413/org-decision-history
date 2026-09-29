-- 개발용 접근 정책 시드. dev 프로파일에서만 적용한다.
-- 플랫폼본부 소속은 자기 조직 문서를 볼 수 있고, 보안본부 소속은 자기 조직만 본다.
-- 권한 필터가 실제로 걸리는지 화면에서 확인하려면 서로 다른 범위가 필요하다.

INSERT INTO access_policy (subject_type, subject_team_id, scope_type, scope_organization_id, can_read)
SELECT 'TEAM', t.id, 'ORGANIZATION', o.id, TRUE
FROM team t JOIN organization o ON o.id = t.organization_id;

-- 시스템 관리자는 역할만으로 전 조직을 본다
INSERT INTO access_policy (subject_type, subject_role, scope_type, scope_organization_id, can_read)
SELECT 'ROLE', 'SYSTEM_ADMIN', 'ORGANIZATION', id, TRUE FROM organization;

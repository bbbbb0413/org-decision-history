-- 로컬 개발용 시드. dev 프로파일에서만 적용한다.
-- 비밀번호는 모두 'password123' 의 BCrypt 해시다. 운영 데이터가 아니다.

INSERT INTO organization (name) VALUES ('플랫폼본부'), ('보안본부');

INSERT INTO team (organization_id, name)
SELECT o.id, t.name
FROM organization o
JOIN (VALUES ('플랫폼본부', '결제팀'),
             ('플랫폼본부', '인증팀'),
             ('보안본부',   '정보보안팀')) AS t(org, name)
  ON o.name = t.org;

INSERT INTO app_user (email, password_hash, name, organization_id)
SELECT v.email, v.hash, v.name, o.id
FROM (VALUES
    ('admin@company.com',  '$2a$10$U7QZM76d2WT.gH90bPrQ4ex1BwOw2xlRGOFjEXB7dRWeXJx.SCZeO', '김하늘', '플랫폼본부'),
    ('lead@company.com',   '$2a$10$U7QZM76d2WT.gH90bPrQ4ex1BwOw2xlRGOFjEXB7dRWeXJx.SCZeO', '박서준', '플랫폼본부'),
    ('member@company.com', '$2a$10$U7QZM76d2WT.gH90bPrQ4ex1BwOw2xlRGOFjEXB7dRWeXJx.SCZeO', '이도윤', '보안본부')
) AS v(email, hash, name, org)
JOIN organization o ON o.name = v.org;

INSERT INTO user_team (user_id, team_id)
SELECT u.id, t.id
FROM app_user u
JOIN (VALUES ('admin@company.com',  '결제팀'),
             ('lead@company.com',   '결제팀'),
             ('lead@company.com',   '인증팀'),
             ('member@company.com', '정보보안팀')) AS v(email, team)
  ON u.email = v.email
JOIN team t ON t.name = v.team;

INSERT INTO user_role (user_id, role)
SELECT u.id, v.role::system_role
FROM app_user u
JOIN (VALUES ('admin@company.com',  'SYSTEM_ADMIN'),
             ('lead@company.com',   'TEAM_ADMIN'),
             ('member@company.com', 'MEMBER')) AS v(email, role)
  ON u.email = v.email;

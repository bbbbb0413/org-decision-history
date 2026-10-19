-- 상충 심각도 등급을 기록할 때 함께 저장한다.
--
-- 점수를 등급으로 바꾸는 기준(0.85/0.70)이 governance-worker 알림과 대시보드에 따로 있어,
-- 한쪽만 고치면 같은 상충이 화면마다 다른 등급으로 보인다. 그래서 governance-worker 가 기록할 때
-- 등급을 정해 이 컬럼에 쓰고(app/severity.py), API 와 화면은 저장된 등급만 읽는다.
-- 기록 시점 등급이므로 나중에 구간을 바꿔도 이미 기록된 상충의 등급은 바뀌지 않는다.
--
-- 기존 행은 지금까지 쓰던 구간으로 채운다.
ALTER TABLE decision_conflicts ADD COLUMN severity_level VARCHAR(10);

UPDATE decision_conflicts
SET severity_level = CASE
    WHEN severity_score >= 0.85 THEN 'CRITICAL'
    WHEN severity_score >= 0.70 THEN 'HIGH'
    ELSE 'MEDIUM'
END;

ALTER TABLE decision_conflicts ALTER COLUMN severity_level SET NOT NULL;
ALTER TABLE decision_conflicts
    ADD CONSTRAINT ck_conflict_severity_level CHECK (severity_level IN ('CRITICAL', 'HIGH', 'MEDIUM'));

COMMENT ON COLUMN decision_conflicts.severity_level IS
    '기록 시점 심각도 등급(CRITICAL/HIGH/MEDIUM). governance-worker 가 severity_score 와 그때의 구간으로 정한다.';

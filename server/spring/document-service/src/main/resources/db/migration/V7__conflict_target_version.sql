-- 상충이 "target 문서의 어느 판"과 비교한 판정인지 기록한다.
--
-- 사람의 판정(RESOLVED·DISMISSED)은 특정 두 문서 판 사이의 판단이다. 그런데 유일 키에 조항 원문(md5)이
-- 들어가 있어, 같은 두 판을 다시 검사할 때 LLM 이 같은 내용을 다른 문구로 뽑으면 새 OPEN 행이 생기고
-- 알림이 다시 나갔다. governance-worker 는 (source 판, target 판)이 같은 쌍에 이미 사람이 모두 닫은
-- 판정이 있으면 새 행이 그 판정을 이어받게 하고, 알림은 그 쌍·판에서 처음 감지됐을 때만 보낸다.
-- 어느 한쪽 문서가 개정되면 판이 달라지므로 다시 정상 감지·알림이 된다.
--
-- 기존 행은 NULL 로 남는다 — 어느 판과 비교했는지 알 수 없으므로 판정을 이어받는 근거로 쓰지 않는다.
ALTER TABLE decision_conflicts ADD COLUMN target_doc_version INT;

COMMENT ON COLUMN decision_conflicts.target_doc_version IS
    '판정에 쓰인 target 문서(청크)의 version. NULL 은 이 컬럼이 생기기 전 기록이라 판을 알 수 없다는 뜻이다.';

-- 같은 쌍·판의 기존 판정을 찾는 조회용
CREATE INDEX ix_conflict_pair_versions
    ON decision_conflicts (source_doc_id, target_doc_id, source_doc_version, target_doc_version);

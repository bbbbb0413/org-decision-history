-- 상충 판정에 쓰인 원문 텍스트의 해시를 기록한다.
--
-- V7 은 사람의 판정을 (source 판, target 판) 단위로 이어받는다. 그런데 판은 문서 어디를 고쳐도 올라가서,
-- 오타 하나만 고쳐도 사람이 기각한 상충이 새로 감지·알림된다. 그래서 판 대신 "판정에 실제로 쓰인 텍스트"로
-- 같은 판정인지 가린다:
--   source_text_hash: 조항을 뽑은 원문 창(색인 청크를 묶은 것)의 md5
--   target_text_hash: 비교한 target 청크 본문의 md5
-- 두 해시가 같은 기존 행이 모두 사람이 닫은 것이면 새 행은 그 판정을 이어받고 알리지 않는다.
-- 고친 곳이 그 창·청크 밖이면 해시가 같아 다시 알리지 않는다.
--
-- source_doc_version 은 개정 전 판정 정리(SUPERSEDED)에, target_doc_version 은 기록용으로 계속 쓴다.
-- 기존 행은 NULL 로 남는다 — 어떤 텍스트로 판정했는지 알 수 없으므로 이어받는 근거로 쓰지 않는다.
ALTER TABLE decision_conflicts
    ADD COLUMN source_text_hash VARCHAR(32),
    ADD COLUMN target_text_hash VARCHAR(32);

COMMENT ON COLUMN decision_conflicts.source_text_hash IS
    '조항을 뽑은 source 원문 창의 md5. NULL 은 이 컬럼이 생기기 전 기록이다.';
COMMENT ON COLUMN decision_conflicts.target_text_hash IS
    '비교한 target 청크 본문의 md5. NULL 은 이 컬럼이 생기기 전 기록이다.';

-- V7 의 판 기준 조회 인덱스는 더 쓰지 않는다
DROP INDEX IF EXISTS ix_conflict_pair_versions;
CREATE INDEX ix_conflict_pair_texts
    ON decision_conflicts (source_doc_id, target_doc_id, source_text_hash, target_text_hash);

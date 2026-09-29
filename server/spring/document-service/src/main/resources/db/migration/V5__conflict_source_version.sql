-- 상충이 "문서의 어느 판(version)을 보고" 내려진 판정인지 기록한다 (2차 체크리스트 7).
--
-- 그동안은 문서를 개정하면 옛 조항 기준으로 열려 있던 상충이 OPEN 인 채로 남았다.
-- 재감지는 (source_doc_id, target_doc_id, md5(source_clause)) 로 upsert 되는데, 개정으로
-- 조항 원문이 바뀌면 md5 가 달라져 새 행만 생기고 옛 행은 아무도 닫지 않았다.
-- 이제 governance-worker 가 재감지를 마친 뒤, 더 낮은 판을 보고 내려진 OPEN 상충을
-- SUPERSEDED 로 닫는다.
--
-- 기존 행은 NULL 로 남는다 — 어느 판을 보고 판정했는지 알 수 없으므로 자동으로 닫지 않는다.
ALTER TABLE decision_conflicts ADD COLUMN source_doc_version INT;

COMMENT ON COLUMN decision_conflicts.source_doc_version IS
    '판정에 쓰인 source 문서의 version. NULL 은 이 컬럼이 생기기 전 기록이라 판을 알 수 없다는 뜻이다.';

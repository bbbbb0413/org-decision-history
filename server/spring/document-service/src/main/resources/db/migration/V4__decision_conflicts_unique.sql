-- governance-worker가 같은 조항 조합을 다시 감지했을 때(재색인 등) 새 행을 쌓지 않고 갱신하게 한다
-- (기능 F-03, 로드맵 §0 M4). governance-worker Db.record_conflict의 ON CONFLICT가 이 인덱스를 겨냥한다.
--
-- source_clause 는 LLM이 뽑은 조항 원문이라 길 수 있어, btree 유일 인덱스 행 크기 제한을 피하려고
-- 원문 대신 md5 해시에 건다.
CREATE UNIQUE INDEX ux_conflict_pair ON decision_conflicts (source_doc_id, target_doc_id, (md5(source_clause)));

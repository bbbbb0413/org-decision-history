-- 색인 완료 후 stream:index-events 발행 단계 (로드맵 §0 M4)
-- governance-worker가 이 단계에서 실패한 항목도 자동 재시도로 다시 발행받도록 index_stage에 값만 추가한다.
ALTER TYPE index_stage ADD VALUE 'EVENT_PUBLISH';

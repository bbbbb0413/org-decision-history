-- 처리 중 항목의 생존 신호.
-- 러너가 항목을 처리하는 동안 주기적으로 now() 를 쓴다. 이 값이 오래 멈춘 RUNNING 은 워커가 죽은 것이다.
-- 선점 시각(last_attempt_at)으로 판단하면 가장 긴 정상 처리보다 길게(30분) 잡아야 해서, 워커가 죽으면
-- 그 문서가 그만큼 색인되지 않는다. 생존 신호는 처리 시간과 무관하게 짧게 잡을 수 있다.
ALTER TABLE index_job_item ADD COLUMN heartbeat_at TIMESTAMPTZ;

-- 전문검색으로만 걸린 근거는 벡터 코사인 유사도가 없다 (기능 4.1.4, 4.1.10).
-- 이제까지는 RRF 병합 점수를 이 컬럼에 저장했는데, 그 값은 절대적인 유사도를 뜻하지 않아
-- 화면에 오해를 준다. 실제 코사인 유사도만 저장하도록 바꾸고, 없을 수 있으니 NULL 허용한다.
ALTER TABLE answer_source ALTER COLUMN similarity DROP NOT NULL;

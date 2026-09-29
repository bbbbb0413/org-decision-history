-- 전문검색 보조 확장
--
-- PostgreSQL 기본 배포에는 한국어 형태소 분석기가 없다.
-- simple 설정 기반 tsvector 와 pg_trgm 유사도 검색을 함께 써서 고유명사 일치를 확보한다.
-- 이 방식으로 목표 검색 품질에 도달하는지는 5단계 착수 전에 샘플 문서로 재검증한다.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

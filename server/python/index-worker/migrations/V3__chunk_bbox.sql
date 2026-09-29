-- PDF 청크의 좌표 기반 출처 표시 (F-07, 로드맵 §0 M5)
-- PDF만 채운다. md·txt·docx·본문 입력 청크는 페이지 개념이 없어 NULL로 남는다.
ALTER TABLE document_chunk
    ADD COLUMN page_start SMALLINT,
    ADD COLUMN page_end   SMALLINT,
    -- [{page, x, y, w, h}] 목록. x·y·w·h는 페이지 좌상단 원점, 0~1로 정규화한 좌표계다
    ADD COLUMN bboxes     JSONB;

"""indexing 스키마 마이그레이션.

적용된 V1 은 한 글자도 바뀌면 안 된다. Flyway 는 기록된 checksum 과 파일이 다르면 기동을 거부한다.
"""
import re
import zlib

from conftest import MIGRATION

# 적용된 DB 의 flyway_schema_history 에 기록된 값
RECORDED_CHECKSUM = 35114448


def flyway_checksum(data: bytes) -> int:
    """Flyway ChecksumCalculator 와 같다. 줄 끝 문자를 뺀 각 줄의 UTF-8 바이트로 CRC32 를 누적한다."""
    text = data.decode("utf-8").removeprefix("﻿")
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()  # BufferedReader.readLine 은 마지막 줄 끝 문자 뒤에 빈 줄을 만들지 않는다
    crc = 0
    for line in lines:
        crc = zlib.crc32(line.encode("utf-8"), crc)
    return crc - (1 << 32) if crc >= (1 << 31) else crc


def test_checksum_matches_recorded_flyway_history():
    assert flyway_checksum(MIGRATION.read_bytes()) == RECORDED_CHECKSUM


async def test_migration_creates_indexing_objects(pool):
    tables = await pool.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'indexing' ORDER BY table_name"
    )
    assert [row["table_name"] for row in tables] == ["document_chunk", "index_job", "index_job_item"]


async def test_v2_adds_event_publish_stage(pool):
    """V2: 색인 완료 이벤트 발행 단계. index_job_item.stage 로 기록할 수 있어야 한다."""
    values = await pool.fetch(
        "SELECT unnest(enum_range(NULL::indexing.index_stage))::text AS v"
    )
    assert "EVENT_PUBLISH" in {row["v"] for row in values}


async def test_v3_adds_chunk_bbox_columns(pool):
    """V3: PDF 좌표 기반 출처 표시."""
    columns = await pool.fetch(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'indexing' AND table_name = 'document_chunk'"
        " AND column_name IN ('page_start', 'page_end', 'bboxes')"
    )
    by_name = {row["column_name"]: row["data_type"] for row in columns}
    assert by_name == {"page_start": "smallint", "page_end": "smallint", "bboxes": "jsonb"}

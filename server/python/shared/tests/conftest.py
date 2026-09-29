import json
import os
from pathlib import Path

import pytest
import redis.asyncio as redis


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    """contracts/indexing/golden. 테스트 파일 위치에서 저장소 루트까지 올라가며 찾는다."""
    for directory in Path(__file__).resolve().parents:
        golden = directory / "contracts" / "indexing" / "golden"
        if (golden / "cases.json").is_file():
            return golden
    raise RuntimeError("contracts/indexing/golden/cases.json 을 찾을 수 없다")


@pytest.fixture(scope="session")
def contracts_dir(golden_dir: Path) -> Path:
    return golden_dir.parent.parent


@pytest.fixture(scope="session")
def load_json():
    def load(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    return load


@pytest.fixture
async def redis_client():
    """스트림 테스트는 실제 Redis 가 필요하다(fakeredis 는 전달 횟수를 돌려주지 않는다).

    docker run -d --rm -p 56379:6379 redis:7.4-alpine
    TEST_REDIS_URL=redis://localhost:56379/15 python -m pytest
    """
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("CI 에서는 TEST_REDIS_URL 이 필요하다. 건너뛰면 스트림 테스트가 조용히 빠진다")
        pytest.skip("TEST_REDIS_URL 이 없어 Redis 통합 테스트를 건너뛴다")
    client = redis.from_url(url, decode_responses=True)
    yield client
    await client.aclose()

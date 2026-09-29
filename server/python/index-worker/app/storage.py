"""객체 저장소에서 원본 파일을 읽는다. 읽기만 한다."""
import asyncio

import boto3
from botocore.config import Config

from app.settings import Settings


class ObjectStorage:
    def __init__(self, settings: Settings):
        self._bucket = settings.storage_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint,
            region_name=settings.storage_region,
            aws_access_key_id=settings.minio_root_user,
            aws_secret_access_key=settings.minio_root_password,
            # MinIO 는 가상 호스트 방식 주소를 쓰지 않는다
            config=Config(s3={"addressing_style": "path"}, connect_timeout=5, read_timeout=60),
        )

    async def read(self, storage_key: str) -> bytes:
        # boto3 는 동기 클라이언트라 이벤트 루프를 막지 않게 스레드에서 돌린다
        return await asyncio.to_thread(self._read, storage_key)

    def _read(self, storage_key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=storage_key)
        return response["Body"].read()

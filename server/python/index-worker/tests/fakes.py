"""테스트 대역."""
from history_shared.events import IndexEvent

from app.documents import DocumentSource


class FakeIndexer:
    """문서별로 던질 예외를 정해 둘 수 있는 색인기."""

    def __init__(self):
        self.errors: dict[int, Exception] = {}
        self.calls: list[int] = []

    async def index(self, document_id: int) -> int:
        self.calls.append(document_id)
        error = self.errors.get(document_id)
        if error is not None:
            raise error
        return 1


class FakeEvents:
    """publish 호출을 기록한다. fail=True면 항상 예외를 던진다."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.published: list[IndexEvent] = []

    async def publish(self, event: IndexEvent) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.published.append(event)


class FakeFiles:
    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})

    async def read(self, storage_key: str) -> bytes:
        if storage_key not in self.files:
            raise OSError(f"NoSuchKey: {storage_key}")
        return self.files[storage_key]


def source(document_id: int, version: int = 1) -> DocumentSource:
    return DocumentSource(
        id=document_id,
        version=version,
        title="문서",
        content="",
        authored_at=None,
        project_name=None,
        organization_id=1,
        team_id=None,
        document_type="MEETING_NOTE",
        storage_key=None,
        original_filename=None,
        deleted=False,
    )

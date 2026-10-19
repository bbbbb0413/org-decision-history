"""auth-service 내부 API 클라이언트.

Spring 서비스의 PermissionScopeClient·AuditClient(common/internal)와 같은 역할이다.
권한 판정은 auth-service 한 곳에 두고 물어본다 — 이 서비스가 권한 테이블을 직접 읽으면
판정 규칙이 두 군데로 갈라진다.
"""
from dataclasses import dataclass

import httpx
import structlog
from cachetools import TTLCache

from app.settings import Settings

log = structlog.get_logger()


@dataclass(frozen=True)
class PermissionScope:
    organization_ids: frozenset[int] = frozenset()
    team_ids: frozenset[int] = frozenset()
    document_ids: frozenset[int] = frozenset()
    denied_document_ids: frozenset[int] = frozenset()
    system_admin: bool = False

    @staticmethod
    def empty() -> "PermissionScope":
        return PermissionScope()

    @staticmethod
    def from_json(data: dict) -> "PermissionScope":
        return PermissionScope(
            organization_ids=frozenset(data.get("organizationIds") or []),
            team_ids=frozenset(data.get("teamIds") or []),
            document_ids=frozenset(data.get("documentIds") or []),
            denied_document_ids=frozenset(data.get("deniedDocumentIds") or []),
            system_admin=bool(data.get("systemAdmin", False)),
        )

    def is_empty(self) -> bool:
        return not self.system_admin and not (
            self.organization_ids or self.team_ids or self.document_ids
        )

    def to_scope_keys(self) -> list[str]:
        if self.system_admin:
            return ["all"]
        keys = (
            [f"org:{i}" for i in self.organization_ids]
            + [f"team:{i}" for i in self.team_ids]
            + [f"doc:{i}" for i in self.document_ids]
        )
        return sorted(keys)


class AuthClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(base_url=settings.auth_service_url, timeout=5.0)
        # 수동 TTL 검사로는 오래된 항목이 지워지지 않고 계속 쌓인다.
        # TTLCache 가 만료·최대 크기 둘 다 알아서 쫓아낸다
        self._cache: TTLCache[int, PermissionScope] = TTLCache(
            maxsize=settings.permission_cache_max_entries, ttl=settings.permission_cache_ttl_seconds
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def scope_of(self, user_id: int) -> PermissionScope:
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        try:
            resp = await self._client.get(
                f"/internal/permission-scope/{user_id}",
                headers={"X-Internal-Key": self._settings.internal_api_key},
            )
            resp.raise_for_status()
            scope = PermissionScope.from_json(resp.json())
            self._cache[user_id] = scope
            return scope
        except Exception as e:  # noqa: BLE001 — 권한 확인 실패는 빈 범위로 처리한다
            log.warning("권한 범위 조회 실패. 빈 범위로 처리한다", user_id=user_id, error=str(e))
            return PermissionScope.empty()

    async def record_audit(
        self,
        actor_id: int,
        action: str,
        target_type: str,
        target_id: int | None,
        allowed: bool,
        reason: str,
    ) -> None:
        try:
            resp = await self._client.post(
                "/internal/audit",
                headers={"X-Internal-Key": self._settings.internal_api_key},
                json={
                    "actorId": actor_id,
                    "action": action,
                    "targetType": target_type,
                    "targetId": target_id,
                    "allowed": allowed,
                    "reason": reason,
                },
            )
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001 — 감사 기록 실패가 답변 생성을 막지 않는다
            log.warning("감사 기록 실패", action=action, error=str(e))

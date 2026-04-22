"""最简 Bearer 认证和权限校验。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, status

from app.store import JsonDataStore


@dataclass(frozen=True)
class CurrentUser:
    """当前请求用户。"""

    role: str
    owner: str | None = None

    @property
    def is_admin(self) -> bool:
        """是否为管理员。"""

        return self.role == "admin"


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    """从 Bearer token 解析当前用户。"""

    if authorization is None:
        _raise_unauthorized("missing bearer token")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        _raise_unauthorized("invalid bearer token")

    token = token.strip()
    if token == "admin":
        return CurrentUser(role="admin")

    if token.startswith("user:"):
        owner = token.removeprefix("user:").strip()
        if owner:
            return CurrentUser(role="user", owner=owner)

    _raise_unauthorized("invalid bearer token")


def require_admin_user(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """要求当前请求必须是管理员。"""

    if current_user.is_admin:
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="platform resources are only available to admin users",
    )


def resolve_service_owner_filter(current_user: CurrentUser, requested_owner: str | None) -> str | None:
    """解析当前请求可用的 owner 过滤条件。"""

    if current_user.is_admin:
        return requested_owner

    if requested_owner is not None and requested_owner != current_user.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"user '{current_user.owner}' cannot query services owned by '{requested_owner}'",
        )
    return current_user.owner


def ensure_service_access(
    store: JsonDataStore,
    current_user: CurrentUser,
    service_name: str,
) -> dict[str, Any]:
    """校验当前用户是否可访问指定服务。"""

    service_detail = store.get_service_detail(service_name)
    if service_detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service '{service_name}' not found",
        )

    if current_user.is_admin:
        return service_detail

    if service_detail.get("owner") != current_user.owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"user '{current_user.owner}' cannot access service '{service_name}'",
        )
    return service_detail


def ensure_task_access(store: JsonDataStore, current_user: CurrentUser, task: dict[str, Any]) -> None:
    """校验当前用户是否可访问指定任务。"""

    if current_user.is_admin:
        return

    if task.get("resourceType") != "service":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="non-admin users cannot access non-service tasks",
        )

    resource_name = task.get("resourceName")
    if not isinstance(resource_name, str) or not resource_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="task is not bound to an accessible service resource",
        )
    ensure_service_access(store, current_user, resource_name)


def _raise_unauthorized(detail: str) -> None:
    """抛出标准 401 响应。"""

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

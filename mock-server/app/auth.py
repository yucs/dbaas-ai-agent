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
    user: str | None = None
    actor_user: str | None = None
    actor_role: str | None = None

    @property
    def is_admin(self) -> bool:
        """是否为管理员。"""

        return self.role == "admin"


def get_current_user(
    authorization: str | None = Header(default=None),
    actor_user: str | None = Header(default=None, alias="X-DBAAS-Actor-User"),
    actor_role: str | None = Header(default=None, alias="X-DBAAS-Actor-Role"),
) -> CurrentUser:
    """从 Bearer token 解析当前用户。"""

    if authorization is None:
        _raise_unauthorized("missing bearer token")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        _raise_unauthorized("invalid bearer token")

    token = token.strip()
    if token == "admin":
        actor_user_value, actor_role_value = _require_actor(actor_user, actor_role)
        if actor_role_value not in {"admin", "system"}:
            _raise_unauthorized("invalid actor role for admin token")
        return CurrentUser(
            role="admin",
            actor_user=actor_user_value,
            actor_role=actor_role_value,
        )

    if token == "user":
        actor_user_value, actor_role_value = _require_actor(actor_user, actor_role)
        if actor_role_value != "user":
            _raise_unauthorized("invalid actor role for user token")
        return CurrentUser(
            role="user",
            user=actor_user_value,
            actor_user=actor_user_value,
            actor_role=actor_role_value,
        )

    if token.startswith("user:"):
        user = token.removeprefix("user:").strip()
        if user:
            actor_user_value = actor_user.strip() if actor_user else user
            actor_role_value = actor_role.strip().lower() if actor_role else "user"
            return CurrentUser(
                role="user",
                user=user,
                actor_user=actor_user_value,
                actor_role=actor_role_value,
            )

    _raise_unauthorized("invalid bearer token")


def require_admin_user(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """要求当前请求必须是管理员。"""

    if current_user.is_admin:
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="platform resources are only available to admin users",
    )


def resolve_service_user_filter(current_user: CurrentUser, requested_user: str | None) -> str | None:
    """解析当前请求可用的 user 过滤条件。"""

    if current_user.is_admin:
        return requested_user

    if requested_user is not None and requested_user != current_user.user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"user '{current_user.user}' cannot query services for user '{requested_user}'",
        )
    return current_user.user


def ensure_service_access(
    store: JsonDataStore,
    current_user: CurrentUser,
    service_name: str,
) -> dict[str, Any]:
    """校验当前用户是否可访问指定服务。"""

    service_detail = store.get_service_seed(service_name)
    if service_detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"service '{service_name}' not found",
        )

    if current_user.is_admin:
        return service_detail

    if service_detail.get("user") != current_user.user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"user '{current_user.user}' cannot access service '{service_name}'",
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


def ensure_user_access(current_user: CurrentUser, user: str) -> None:
    """校验当前用户是否可访问指定用户信息。"""

    if current_user.is_admin or current_user.user == user:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"user '{current_user.user}' cannot access user '{user}'",
    )


def _require_actor(actor_user: str | None, actor_role: str | None) -> tuple[str, str]:
    """校验并返回 DBAAS 调用发起者。"""

    actor_user_value = actor_user.strip() if actor_user else ""
    actor_role_value = actor_role.strip().lower() if actor_role else ""
    if not actor_user_value:
        _raise_unauthorized("missing X-DBAAS-Actor-User header")
    if not actor_role_value:
        _raise_unauthorized("missing X-DBAAS-Actor-Role header")
    return actor_user_value, actor_role_value


def _raise_unauthorized(detail: str) -> None:
    """抛出标准 401 响应。"""

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )

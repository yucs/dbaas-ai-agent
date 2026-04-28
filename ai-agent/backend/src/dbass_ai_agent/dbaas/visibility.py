from __future__ import annotations

from pathlib import Path
from typing import Any

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .constants import SERVICES_KIND
from .locks import ResourceFileLock, ResourceLockTimeoutError, lock_exists
from .schema import schema_path, schema_version, validate_payload
from .sync import DbaasServiceSynchronizer, isoformat, utcnow
from .workspace import DbaasWorkspace, delete_if_exists, read_json_file, write_json_atomic, write_meta_atomic


class DbaasVisibilityError(RuntimeError):
    """Raised when a user-scoped DBAAS view cannot be generated."""


def ensure_visible_services(
    config: DbaasConfig,
    identity: Identity,
    *,
    app_root: Path = APP_ROOT,
) -> dict[str, Any]:
    synchronizer = DbaasServiceSynchronizer(config, app_root=app_root)
    admin_meta = synchronizer.ensure_admin_services()
    if admin_meta.get("status") != "fresh":
        return admin_meta

    if identity.role == "admin":
        return {
            **admin_meta,
            "scope": "admin",
            "message": "服务列表快照可用，当前用户为管理员，可查询全量服务。",
        }

    return _ensure_user_services(config, identity, admin_meta, app_root=app_root)


def _ensure_user_services(
    config: DbaasConfig,
    identity: Identity,
    admin_meta: dict[str, Any],
    *,
    app_root: Path,
) -> dict[str, Any]:
    workspace = DbaasWorkspace(config)
    user_id = identity.user_id
    user_meta = _read_user_meta(workspace, user_id)
    data_path = workspace.data_path(SERVICES_KIND, user_id=user_id)
    if _user_meta_matches_source(user_meta, admin_meta) and data_path.exists():
        return {
            **user_meta,
            "message": "服务列表快照可用，当前路径只包含该用户可见服务。",
        }

    lock_path = workspace.lock_path(SERVICES_KIND, user_id=user_id)
    try:
        with ResourceFileLock(lock_path, timeout_seconds=config.resource_lock_timeout_seconds):
            user_meta = _read_user_meta(workspace, user_id)
            if _user_meta_matches_source(user_meta, admin_meta) and data_path.exists():
                return {
                    **user_meta,
                    "message": "服务列表快照可用，当前路径只包含该用户可见服务。",
                }

            delete_if_exists(data_path)
            admin_payload = read_json_file(Path(str(admin_meta["data_path"])))
            if not isinstance(admin_payload, list):
                raise DbaasVisibilityError("admin services snapshot must be an array")
            visible_payload = [
                service
                for service in admin_payload
                if isinstance(service, dict) and service.get("user") == identity.user
            ]
            validate_payload(SERVICES_KIND, visible_payload, app_root=app_root)
            bytes_written = write_json_atomic(data_path, visible_payload)
            now = utcnow()
            meta = {
                "kind": SERVICES_KIND,
                "scope": "user",
                "user_id": user_id,
                "user": identity.user,
                "role": identity.role,
                "source_scope": "admin",
                "source_synced_at": admin_meta.get("synced_at"),
                "filtered_at": isoformat(now),
                "status": "fresh",
                "data_path": str(data_path),
                "meta_path": str(workspace.meta_path(SERVICES_KIND, user_id=user_id)),
                "record_count": len(visible_payload),
                "bytes": bytes_written,
                "schema_version": schema_version(SERVICES_KIND),
                "schema_path": str(schema_path(SERVICES_KIND, app_root=app_root)),
                "message": "服务列表快照可用，当前路径只包含该用户可见服务。",
            }
            write_meta_atomic(workspace.meta_path(SERVICES_KIND, user_id=user_id), meta)
            return meta
    except ResourceLockTimeoutError:
        return {
            "kind": SERVICES_KIND,
            "scope": "user",
            "status": "refreshing",
            "data_path": None,
            "meta_path": str(workspace.meta_path(SERVICES_KIND, user_id=user_id)),
            "refreshing": lock_exists(lock_path),
            "message": "服务列表正在刷新，请稍后重试。",
        }


def _read_user_meta(workspace: DbaasWorkspace, user_id: str) -> dict[str, Any] | None:
    path = workspace.meta_path(SERVICES_KIND, user_id=user_id)
    if not path.exists():
        return None
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        return None
    return payload


def _user_meta_matches_source(
    user_meta: dict[str, Any] | None,
    admin_meta: dict[str, Any],
) -> bool:
    if user_meta is None:
        return False
    return (
        user_meta.get("status") == "fresh"
        and user_meta.get("source_synced_at") == admin_meta.get("synced_at")
    )

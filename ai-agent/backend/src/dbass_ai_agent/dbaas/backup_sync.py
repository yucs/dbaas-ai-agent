from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import httpx

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .auth import dbaas_identity_headers
from .config import DbaasConfig
from .constants import ADMIN_SCOPE, BACKUPS_ENDPOINT, BACKUPS_KIND, USER_SCOPE
from .schema import schema_path, schema_version
from .snapshot_meta import isoformat, is_meta_fresh, read_meta, utcnow
from .workspace import (
    DbaasSnapshotPaths,
    DbaasWorkspace,
    delete_if_exists,
    replace_file_atomic,
    write_json_temp,
    write_meta_atomic,
)


logger = logging.getLogger(__name__)
_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()


class DbaasBackupSynchronizer:
    def __init__(self, config: DbaasConfig, *, app_root: Path = APP_ROOT) -> None:
        self.config = config
        self.workspace = DbaasWorkspace(config)
        self.app_root = app_root

    def ensure_snapshot(self, identity: Identity, *, refresh: bool = False) -> dict[str, Any]:
        if identity.role != ADMIN_SCOPE and not identity.user_id:
            return self._snapshot_unavailable(
                None,
                "当前用户身份缺少可见范围，无法查询备份列表。",
                error_type="permission_denied",
                scope=USER_SCOPE,
                user=None,
            )

        scope = ADMIN_SCOPE if identity.role == ADMIN_SCOPE else USER_SCOPE
        paths = self.workspace.paths(BACKUPS_KIND, scope=scope, user=None if scope == ADMIN_SCOPE else identity.user_id)
        if refresh:
            return self._refresh_under_lock(paths, identity, force=True)

        current = read_meta(paths.meta_path)
        if self._is_snapshot_fresh(paths, current):
            assert current is not None
            return self._snapshot(paths, current)
        return self._refresh_under_lock(paths, identity, force=False)

    def _refresh_under_lock(
        self,
        paths: DbaasSnapshotPaths,
        identity: Identity,
        *,
        force: bool,
    ) -> dict[str, Any]:
        lock = _lock_for(paths.key)
        acquired = lock.acquire(timeout=self.config.user_snapshot_refresh_wait_seconds)
        if not acquired:
            return self._snapshot_unavailable(paths, "当前 DBAAS 备份数据视图正在刷新，等待超时。")
        try:
            current = read_meta(paths.meta_path)
            if not force and self._is_snapshot_fresh(paths, current):
                assert current is not None
                return self._snapshot(paths, current)

            result = self._refresh_backups(paths, identity)
            if result.get("status") == "fresh":
                return result

            message = str(result.get("message") or "DBAAS 备份数据视图刷新失败。")
            error_type = str(result.get("error_type") or "snapshot_unavailable")
            if force and current is not None and self._is_snapshot_fresh(paths, current):
                self._write_fresh_error_meta(paths, current, message)
            else:
                delete_if_exists(paths.data_path)
                self._write_unavailable_meta(paths, message, error_type=error_type)
            return self._snapshot_unavailable(paths, message, error_type=error_type)
        finally:
            lock.release()

    def _refresh_backups(self, paths: DbaasSnapshotPaths, identity: Identity) -> dict[str, Any]:
        url = f"{self.config.server_base_url}{BACKUPS_ENDPOINT}"
        try:
            with httpx.Client(timeout=self.config.request_timeout_seconds, trust_env=False) as client:
                response = client.get(url, headers=dbaas_identity_headers(identity))
        except httpx.HTTPError as exc:
            logger.exception("dbaas backups request failed scope=%s user=%s", paths.scope, paths.user)
            return self._snapshot_unavailable(paths, f"请求 DBAAS 备份接口失败：{exc}", error_type="dbaas_request_failed")

        if response.status_code in {401, 403}:
            return self._snapshot_unavailable(paths, "当前用户无权访问备份列表。", error_type="permission_denied")
        if response.status_code < 200 or response.status_code >= 300:
            return self._snapshot_unavailable(
                paths,
                f"DBAAS 备份接口返回异常状态：{response.status_code}",
                error_type="dbaas_request_failed",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            return self._snapshot_unavailable(paths, f"DBAAS 备份接口返回非 JSON 数据：{exc}", error_type="dbaas_request_failed")

        shape_error = _validate_backup_payload_shape(payload)
        if shape_error is not None:
            return self._snapshot_unavailable(paths, shape_error, error_type="snapshot_unavailable")

        data_tmp_path: Path | None = None
        meta_tmp_path: Path | None = None
        now = utcnow()
        try:
            data_tmp_path, bytes_written = write_json_temp(paths.data_path, payload)
            meta = {
                "kind": BACKUPS_KIND,
                "scope": paths.scope,
                "user": paths.user,
                "version": 1,
                "data_path": str(paths.data_path),
                "meta_path": str(paths.meta_path),
                "status": "fresh",
                "synced_at": isoformat(now),
                "expires_at": isoformat(now + self._ttl_delta()),
                "ttl_seconds": self.config.backup_snapshot_ttl_seconds,
                "record_count": len(payload),
                "bytes": bytes_written,
                "source": "dbaas-server",
                "source_endpoint": BACKUPS_ENDPOINT,
                "schema_version": schema_version(BACKUPS_KIND, scope=paths.scope),
                "schema_path": str(schema_path(BACKUPS_KIND, app_root=self.app_root, scope=paths.scope)),
                "last_refresh_status": "success",
                "last_error": None,
            }
            meta_tmp_path, _ = write_json_temp(paths.meta_path, meta)
            replace_file_atomic(data_tmp_path, paths.data_path)
            replace_file_atomic(meta_tmp_path, paths.meta_path)
            logger.info(
                "dbaas backups snapshot refreshed scope=%s user=%s records=%s bytes=%s",
                paths.scope,
                paths.user or "-",
                meta["record_count"],
                bytes_written,
            )
            return meta
        except Exception as exc:  # noqa: BLE001
            if data_tmp_path is not None:
                delete_if_exists(data_tmp_path)
            if meta_tmp_path is not None:
                delete_if_exists(meta_tmp_path)
            logger.exception("dbaas backups snapshot write failed scope=%s user=%s", paths.scope, paths.user)
            return self._snapshot_unavailable(paths, f"写入 DBAAS 备份数据失败：{exc}", error_type="snapshot_unavailable")

    def _ttl_delta(self):
        from datetime import timedelta

        return timedelta(seconds=self.config.backup_snapshot_ttl_seconds)

    def _is_snapshot_fresh(self, paths: DbaasSnapshotPaths, meta: dict[str, Any] | None) -> bool:
        return (
            meta is not None
            and is_meta_fresh(meta)
            and meta.get("scope") == paths.scope
            and meta.get("user") == paths.user
            and meta.get("data_path") == str(paths.data_path)
            and meta.get("schema_version") == schema_version(BACKUPS_KIND, scope=paths.scope)
            and meta.get("schema_path") == str(schema_path(BACKUPS_KIND, app_root=self.app_root, scope=paths.scope))
            and paths.data_path.exists()
            and paths.meta_path.exists()
        )

    def _snapshot(self, paths: DbaasSnapshotPaths, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            **meta,
            "kind": BACKUPS_KIND,
            "scope": paths.scope,
            "user": paths.user,
            "status": "fresh",
            "data_path": str(paths.data_path),
            "meta_path": str(paths.meta_path),
        }

    def _write_fresh_error_meta(self, paths: DbaasSnapshotPaths, current: dict[str, Any], message: str) -> None:
        meta = {
            **current,
            "last_refresh_status": "error",
            "last_error": message,
        }
        write_meta_atomic(paths.meta_path, meta)

    def _write_unavailable_meta(self, paths: DbaasSnapshotPaths, message: str, *, error_type: str) -> None:
        meta = {
            "kind": BACKUPS_KIND,
            "scope": paths.scope,
            "user": paths.user,
            "version": 1,
            "status": "error",
            "error_type": error_type,
            "data_path": None,
            "meta_path": str(paths.meta_path),
            "synced_at": isoformat(utcnow()),
            "expires_at": isoformat(utcnow() + self._ttl_delta()),
            "ttl_seconds": self.config.backup_snapshot_ttl_seconds,
            "record_count": 0,
            "bytes": 0,
            "source": "dbaas-server",
            "source_endpoint": BACKUPS_ENDPOINT,
            "schema_version": schema_version(BACKUPS_KIND, scope=paths.scope),
            "schema_path": str(schema_path(BACKUPS_KIND, app_root=self.app_root, scope=paths.scope)),
            "last_refresh_status": "error",
            "last_error": message,
            "message": message,
        }
        write_meta_atomic(paths.meta_path, meta)

    def _snapshot_unavailable(
        self,
        paths: DbaasSnapshotPaths | None,
        message: str,
        *,
        error_type: str = "snapshot_unavailable",
        scope: str | None = None,
        user: str | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": BACKUPS_KIND,
            "scope": paths.scope if paths is not None else scope,
            "user": paths.user if paths is not None else user,
            "status": "error",
            "error_type": error_type,
            "data_path": None,
            "meta_path": str(paths.meta_path) if paths is not None else None,
            "message": f"当前没有可用的 DBAAS 备份数据视图，暂时无法获得准确数据：{message}",
        }


def _validate_backup_payload_shape(payload: Any) -> str | None:
    if not isinstance(payload, list):
        return "DBAAS 备份接口返回结构不是数组。"
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return f"DBAAS 备份接口第 {index} 条记录不是对象。"
    return None


def _lock_for(key: str) -> threading.Lock:
    with _refresh_locks_guard:
        lock = _refresh_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _refresh_locks[key] = lock
        return lock

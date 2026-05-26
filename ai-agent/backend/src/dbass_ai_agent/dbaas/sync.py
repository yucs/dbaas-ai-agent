from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from dbass_ai_agent.config import APP_ROOT

from .config import DbaasConfig
from .constants import ADMIN_SCOPE, SERVICES_ENDPOINT, SERVICES_KIND, USER_SCOPE
from .schema import DbaasSchemaError, schema_path, schema_version, validate_payload
from .workspace import (
    DbaasSnapshotPaths,
    DbaasWorkspace,
    delete_if_exists,
    read_json_file,
    replace_file_atomic,
    write_json_temp,
)


logger = logging.getLogger(__name__)
_user_refresh_locks: dict[str, threading.Lock] = {}
_user_refresh_locks_guard = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def isoformat(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_meta_fresh(meta: dict[str, Any], *, now: datetime | None = None) -> bool:
    if meta.get("status") != "fresh":
        return False
    expires_at = meta.get("expires_at")
    if not isinstance(expires_at, str):
        return False
    try:
        return (now or utcnow()) <= parse_time(expires_at)
    except ValueError:
        return False


def read_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = read_json_file(path)
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


class DbaasServiceSynchronizer:
    def __init__(self, config: DbaasConfig, *, app_root: Path = APP_ROOT) -> None:
        self.config = config
        self.workspace = DbaasWorkspace(config)
        self.app_root = app_root

    def force_refresh_admin_services(self) -> dict[str, Any]:
        paths = self.workspace.paths(SERVICES_KIND, scope=ADMIN_SCOPE)
        return self._force_refresh_services(paths)

    def refresh_admin_services(self) -> dict[str, Any]:
        paths = self.workspace.paths(SERVICES_KIND, scope=ADMIN_SCOPE)
        return self._refresh_services(paths)

    def force_refresh_user_services(self, user: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        paths = self.workspace.paths(SERVICES_KIND, scope=USER_SCOPE, user=user)
        lock = _user_lock_for(paths.key)
        timeout = self.config.user_snapshot_refresh_wait_seconds if timeout_seconds is None else timeout_seconds
        acquired = lock.acquire(timeout=max(0, timeout))
        if not acquired:
            return self._snapshot_unavailable_meta(paths, "当前用户服务列表快照正在刷新，等待超时。")
        try:
            return self._force_refresh_services(paths)
        finally:
            lock.release()

    def refresh_user_services(self, user: str) -> dict[str, Any]:
        paths = self.workspace.paths(SERVICES_KIND, scope=USER_SCOPE, user=user)
        lock = _user_lock_for(paths.key)
        with lock:
            return self._refresh_services(paths)

    def delete_user_services_snapshot(self, user: str) -> None:
        paths = self.workspace.paths(SERVICES_KIND, scope=USER_SCOPE, user=user)
        lock = _user_lock_for(paths.key)
        with lock:
            self._delete_files(paths)

    def _force_refresh_services(self, paths: DbaasSnapshotPaths) -> dict[str, Any]:
        current = read_meta(paths.meta_path)
        has_fresh = self._is_snapshot_fresh(paths, current)
        try:
            return self._refresh_services(paths)
        except Exception as exc:  # noqa: BLE001
            logger.exception("dbaas services refresh failed scope=%s user=%s", paths.scope, paths.user)
            message = str(exc)
            if has_fresh and current is not None:
                return self._write_fresh_error_meta(paths, current, message)
            delete_if_exists(paths.data_path)
            return self._write_unavailable_meta(paths, message)

    def _refresh_services(self, paths: DbaasSnapshotPaths) -> dict[str, Any]:
        payload = self._fetch_services(paths)
        try:
            validate_payload(SERVICES_KIND, payload, app_root=self.app_root, scope=paths.scope)
        except DbaasSchemaError:
            logger.exception("dbaas services schema validation failed scope=%s user=%s", paths.scope, paths.user)
            raise

        now = utcnow()
        record_count = len(payload) if isinstance(payload, list) else 0
        data_tmp_path: Path | None = None
        meta_tmp_path: Path | None = None
        try:
            data_tmp_path, bytes_written = write_json_temp(paths.data_path, payload)
            meta = {
                "kind": SERVICES_KIND,
                "scope": paths.scope,
                "user": paths.user,
                "version": 1,
                "data_path": str(paths.data_path),
                "meta_path": str(paths.meta_path),
                "status": "fresh",
                "synced_at": isoformat(now),
                "expires_at": isoformat(now + timedelta(seconds=self.config.ttl_seconds)),
                "ttl_seconds": self.config.ttl_seconds,
                "record_count": record_count,
                "bytes": bytes_written,
                "source": "dbaas-server",
                "source_endpoint": SERVICES_ENDPOINT,
                "schema_version": schema_version(SERVICES_KIND, scope=paths.scope),
                "schema_path": str(schema_path(SERVICES_KIND, app_root=self.app_root, scope=paths.scope)),
                "last_refresh_status": "success",
                "last_error": None,
            }
            meta_tmp_path, _ = write_json_temp(paths.meta_path, meta)
            replace_file_atomic(data_tmp_path, paths.data_path)
            replace_file_atomic(meta_tmp_path, paths.meta_path)
        except Exception:
            if data_tmp_path is not None:
                delete_if_exists(data_tmp_path)
            if meta_tmp_path is not None:
                delete_if_exists(meta_tmp_path)
            raise
        logger.info(
            "dbaas services snapshot refreshed scope=%s user=%s records=%s bytes=%s",
            paths.scope,
            paths.user or "-",
            record_count,
            bytes_written,
        )
        return meta

    def _fetch_services(self, paths: DbaasSnapshotPaths) -> Any:
        url = f"{self.config.server_base_url}{SERVICES_ENDPOINT}"
        with httpx.Client(timeout=self.config.request_timeout_seconds, trust_env=False) as client:
            response = client.get(url, headers=_identity_headers(paths))
            response.raise_for_status()
            return response.json()

    def _is_snapshot_fresh(self, paths: DbaasSnapshotPaths, meta: dict[str, Any] | None) -> bool:
        return (
            meta is not None
            and is_meta_fresh(meta)
            and meta.get("scope") == paths.scope
            and meta.get("user") == paths.user
            and meta.get("data_path") == str(paths.data_path)
            and meta.get("schema_version") == schema_version(SERVICES_KIND, scope=paths.scope)
            and paths.data_path.exists()
            and paths.meta_path.exists()
        )

    def _delete_files(self, paths: DbaasSnapshotPaths) -> None:
        delete_if_exists(paths.data_path)
        delete_if_exists(paths.meta_path)

    def _write_fresh_error_meta(
        self,
        paths: DbaasSnapshotPaths,
        current: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        meta = {
            **current,
            "last_refresh_status": "error",
            "last_error": message,
        }
        self._write_meta(paths, meta)
        return meta

    def _write_unavailable_meta(self, paths: DbaasSnapshotPaths, message: str) -> dict[str, Any]:
        meta = self._snapshot_unavailable_meta(paths, message)
        self._write_meta(paths, meta)
        return meta

    def _snapshot_unavailable_meta(self, paths: DbaasSnapshotPaths, message: str) -> dict[str, Any]:
        return {
            "kind": SERVICES_KIND,
            "scope": paths.scope,
            "user": paths.user,
            "version": 1,
            "status": "error",
            "error_type": "snapshot_unavailable",
            "data_path": None,
            "meta_path": str(paths.meta_path),
            "synced_at": None,
            "expires_at": None,
            "ttl_seconds": self.config.ttl_seconds,
            "record_count": 0,
            "bytes": 0,
            "source": "dbaas-server",
            "source_endpoint": SERVICES_ENDPOINT,
            "schema_version": schema_version(SERVICES_KIND, scope=paths.scope),
            "schema_path": str(schema_path(SERVICES_KIND, app_root=self.app_root, scope=paths.scope)),
            "last_refresh_status": "error",
            "last_error": message,
            "message": f"当前没有可用的服务列表快照，可能拉取 DBAAS 数据失败：{message}",
        }

    def _write_meta(self, paths: DbaasSnapshotPaths, meta: dict[str, Any]) -> None:
        tmp_path: Path | None = None
        try:
            tmp_path, _ = write_json_temp(paths.meta_path, meta)
            replace_file_atomic(tmp_path, paths.meta_path)
        except Exception:
            if tmp_path is not None:
                delete_if_exists(tmp_path)
            raise


def _identity_headers(paths: DbaasSnapshotPaths) -> dict[str, str]:
    if paths.scope == ADMIN_SCOPE:
        return {"Authorization": "Bearer admin"}
    if not paths.user:
        raise ValueError("ordinary user services refresh requires user identity")
    return {"Authorization": f"Bearer user:{paths.user}"}


def _user_lock_for(key: str) -> threading.Lock:
    with _user_refresh_locks_guard:
        lock = _user_refresh_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _user_refresh_locks[key] = lock
        return lock

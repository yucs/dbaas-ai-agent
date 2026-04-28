from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from dbass_ai_agent.config import APP_ROOT

from .config import DbaasConfig
from .constants import SERVICES_ENDPOINT, SERVICES_KIND
from .locks import ResourceFileLock, ResourceLockTimeoutError, lock_exists
from .schema import DbaasSchemaError, schema_path, schema_version, validate_payload
from .workspace import DbaasWorkspace, delete_if_exists, read_json_file, write_json_atomic, write_meta_atomic


logger = logging.getLogger(__name__)


class DbaasSyncError(RuntimeError):
    """Raised when a DBAAS snapshot cannot be synchronized."""


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
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        return None
    return payload


class DbaasServiceSynchronizer:
    def __init__(self, config: DbaasConfig, *, app_root: Path = APP_ROOT) -> None:
        self.config = config
        self.workspace = DbaasWorkspace(config)
        self.app_root = app_root

    def ensure_admin_services(self) -> dict[str, Any]:
        current = self._current_admin_meta()
        if current is not None and is_meta_fresh(current) and self.workspace.data_path(SERVICES_KIND).exists():
            return current

        lock_path = self.workspace.lock_path(SERVICES_KIND)
        try:
            with ResourceFileLock(
                lock_path,
                timeout_seconds=self.config.resource_lock_timeout_seconds,
            ):
                current = self._current_admin_meta()
                if (
                    current is not None
                    and is_meta_fresh(current)
                    and self.workspace.data_path(SERVICES_KIND).exists()
                ):
                    return current
                self._delete_expired_admin_files()
                return self.refresh_admin_services()
        except ResourceLockTimeoutError as exc:
            return self._refreshing_meta()
        except Exception as exc:
            logger.exception("dbaas services sync failed")
            meta = self._error_meta(str(exc))
            write_meta_atomic(self.workspace.meta_path(SERVICES_KIND), meta)
            raise DbaasSyncError(str(exc)) from exc

    def force_refresh_admin_services(self) -> dict[str, Any]:
        lock_path = self.workspace.lock_path(SERVICES_KIND)
        try:
            with ResourceFileLock(
                lock_path,
                timeout_seconds=self.config.resource_lock_timeout_seconds,
            ):
                return self.refresh_admin_services()
        except ResourceLockTimeoutError:
            return self._refreshing_meta()
        except Exception as exc:
            logger.exception("dbaas services force refresh failed")
            meta = self._error_meta(str(exc))
            write_meta_atomic(self.workspace.meta_path(SERVICES_KIND), meta)
            raise DbaasSyncError(str(exc)) from exc

    def refresh_admin_services(self) -> dict[str, Any]:
        payload = self._fetch_services()
        try:
            validate_payload(SERVICES_KIND, payload, app_root=self.app_root)
        except DbaasSchemaError:
            logger.exception("dbaas services schema validation failed")
            raise

        now = utcnow()
        data_path = self.workspace.data_path(SERVICES_KIND)
        bytes_written = write_json_atomic(data_path, payload)
        record_count = len(payload) if isinstance(payload, list) else 0
        meta = {
            "kind": SERVICES_KIND,
            "scope": "admin",
            "version": 1,
            "data_path": str(data_path),
            "meta_path": str(self.workspace.meta_path(SERVICES_KIND)),
            "status": "fresh",
            "synced_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(seconds=self.config.ttl_seconds)),
            "ttl_seconds": self.config.ttl_seconds,
            "record_count": record_count,
            "bytes": bytes_written,
            "source": "dbaas-server",
            "source_endpoint": SERVICES_ENDPOINT,
            "schema_version": schema_version(SERVICES_KIND),
            "schema_path": str(schema_path(SERVICES_KIND, app_root=self.app_root)),
            "last_refresh_status": "success",
            "last_error": None,
        }
        write_meta_atomic(self.workspace.meta_path(SERVICES_KIND), meta)
        logger.info("dbaas services snapshot refreshed records=%s bytes=%s", record_count, bytes_written)
        return meta

    def _fetch_services(self) -> Any:
        url = f"{self.config.server_base_url}{SERVICES_ENDPOINT}"
        with httpx.Client(timeout=self.config.request_timeout_seconds, trust_env=False) as client:
            response = client.get(url, headers={"Authorization": "Bearer admin"})
            response.raise_for_status()
            return response.json()

    def _current_admin_meta(self) -> dict[str, Any] | None:
        return read_meta(self.workspace.meta_path(SERVICES_KIND))

    def _delete_expired_admin_files(self) -> None:
        meta = self._current_admin_meta()
        data_path = self.workspace.data_path(SERVICES_KIND)
        if meta is None or not is_meta_fresh(meta) or not data_path.exists():
            delete_if_exists(data_path)

    def _refreshing_meta(self) -> dict[str, Any]:
        return {
            "kind": SERVICES_KIND,
            "scope": "admin",
            "status": "refreshing",
            "data_path": None,
            "meta_path": str(self.workspace.meta_path(SERVICES_KIND)),
            "refreshing": lock_exists(self.workspace.lock_path(SERVICES_KIND)),
            "message": "服务列表正在刷新，请稍后重试。",
        }

    def _error_meta(self, message: str) -> dict[str, Any]:
        now = utcnow()
        return {
            "kind": SERVICES_KIND,
            "scope": "admin",
            "version": 1,
            "data_path": None,
            "meta_path": str(self.workspace.meta_path(SERVICES_KIND)),
            "status": "error",
            "synced_at": None,
            "expires_at": isoformat(now),
            "ttl_seconds": self.config.ttl_seconds,
            "record_count": 0,
            "bytes": 0,
            "source": "dbaas-server",
            "source_endpoint": SERVICES_ENDPOINT,
            "schema_version": schema_version(SERVICES_KIND),
            "schema_path": str(schema_path(SERVICES_KIND, app_root=self.app_root)),
            "last_refresh_status": "error",
            "last_error": message,
        }

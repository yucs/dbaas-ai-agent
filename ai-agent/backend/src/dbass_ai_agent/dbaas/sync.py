from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from dbass_ai_agent.config import APP_ROOT

from .config import DbaasConfig
from .constants import SERVICES_ENDPOINT, SERVICES_KIND
from .schema import DbaasSchemaError, schema_path, schema_version, validate_payload
from .workspace import (
    DbaasWorkspace,
    delete_if_exists,
    read_json_file,
    replace_file_atomic,
    write_json_temp,
)


logger = logging.getLogger(__name__)


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
        current = self._current_admin_meta()
        if not self._is_admin_snapshot_fresh(current):
            self._delete_admin_files()
        try:
            return self.refresh_admin_services()
        except Exception as exc:
            logger.exception("dbaas services force refresh failed")
            return self._unavailable_meta(str(exc))

    def refresh_admin_services(self) -> dict[str, Any]:
        payload = self._fetch_services()
        try:
            validate_payload(SERVICES_KIND, payload, app_root=self.app_root)
        except DbaasSchemaError:
            logger.exception("dbaas services schema validation failed")
            raise

        now = utcnow()
        data_path = self.workspace.data_path(SERVICES_KIND)
        meta_path = self.workspace.meta_path(SERVICES_KIND)
        record_count = len(payload) if isinstance(payload, list) else 0
        data_tmp_path: Path | None = None
        meta_tmp_path: Path | None = None
        try:
            data_tmp_path, bytes_written = write_json_temp(data_path, payload)
            meta = {
                "kind": SERVICES_KIND,
                "scope": "admin",
                "version": 1,
                "data_path": str(data_path),
                "meta_path": str(meta_path),
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
            meta_tmp_path, _ = write_json_temp(meta_path, meta)
            replace_file_atomic(data_tmp_path, data_path)
            replace_file_atomic(meta_tmp_path, meta_path)
        except Exception:
            if data_tmp_path is not None:
                delete_if_exists(data_tmp_path)
            if meta_tmp_path is not None:
                delete_if_exists(meta_tmp_path)
            raise
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

    def _is_admin_snapshot_fresh(self, meta: dict[str, Any] | None) -> bool:
        return (
            meta is not None
            and is_meta_fresh(meta)
            and self.workspace.data_path(SERVICES_KIND).exists()
            and self.workspace.meta_path(SERVICES_KIND).exists()
        )

    def _delete_admin_files(self) -> None:
        delete_if_exists(self.workspace.data_path(SERVICES_KIND))
        delete_if_exists(self.workspace.meta_path(SERVICES_KIND))

    def _unavailable_meta(self, message: str) -> dict[str, Any]:
        return {
            "kind": SERVICES_KIND,
            "scope": "admin",
            "status": "error",
            "error_type": "snapshot_unavailable",
            "data_path": None,
            "meta_path": str(self.workspace.meta_path(SERVICES_KIND)),
            "synced_at": None,
            "expires_at": None,
            "ttl_seconds": self.config.ttl_seconds,
            "record_count": 0,
            "bytes": 0,
            "source": "dbaas-server",
            "source_endpoint": SERVICES_ENDPOINT,
            "schema_version": schema_version(SERVICES_KIND),
            "schema_path": str(schema_path(SERVICES_KIND, app_root=self.app_root)),
            "last_refresh_status": "error",
            "last_error": message,
            "message": f"当前没有可用的服务列表快照，可能拉取 DBAAS 数据失败：{message}",
        }

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .auth import dbaas_identity_headers
from .config import DbaasConfig
from .constants import ADMIN_SCOPE, CLUSTERS_ENDPOINT, CLUSTERS_KIND
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
_refresh_lock = threading.Lock()


class DbaasClusterSynchronizer:
    def __init__(self, config: DbaasConfig, *, app_root: Path = APP_ROOT) -> None:
        self.config = config
        self.workspace = DbaasWorkspace(config)
        self.app_root = app_root

    def ensure_snapshot(self, identity: Identity, *, refresh: bool = False) -> dict[str, Any]:
        paths = self._paths()
        if identity.role != ADMIN_SCOPE:
            return self._snapshot_unavailable(
                paths,
                "当前身份无权查询平台集群数据。",
                error_type="permission_denied",
            )
        if refresh:
            return self._refresh_under_lock(paths, identity, force=True, error_type="refresh_failed")

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
        error_type: str = "snapshot_unavailable",
    ) -> dict[str, Any]:
        acquired = _refresh_lock.acquire(timeout=self.config.cluster_refresh_lock_timeout_seconds)
        if not acquired:
            return self._snapshot_unavailable(paths, "当前 DBAAS 集群数据视图正在刷新，等待超时。", error_type=error_type)
        try:
            current = read_meta(paths.meta_path)
            if not force and self._is_snapshot_fresh(paths, current):
                assert current is not None
                return self._snapshot(paths, current)

            result = self._refresh_clusters(paths, identity)
            if result.get("status") == "fresh":
                return result

            message = str(result.get("last_error") or result.get("message") or "DBAAS 集群数据视图刷新失败。")
            refresh_error_type = str(result.get("error_type") or error_type)
            if current is not None and self._is_snapshot_fresh(paths, current):
                self._write_fresh_error_meta(paths, current, message)
            else:
                delete_if_exists(paths.data_path)
                self._write_unavailable_meta(paths, message, error_type=refresh_error_type)
            return self._snapshot_unavailable(paths, message, error_type=error_type)
        finally:
            _refresh_lock.release()

    def _refresh_clusters(self, paths: DbaasSnapshotPaths, identity: Identity) -> dict[str, Any]:
        url = f"{self.config.server_base_url}{CLUSTERS_ENDPOINT}"
        try:
            with httpx.Client(timeout=self.config.request_timeout_seconds, trust_env=False) as client:
                response = client.get(url, headers=dbaas_identity_headers(identity))
        except httpx.HTTPError as exc:
            logger.exception("dbaas clusters request failed")
            return self._snapshot_unavailable(paths, f"请求 DBAAS 集群接口失败：{exc}", error_type="dbaas_request_failed")

        if response.status_code in {401, 403}:
            return self._snapshot_unavailable(paths, "当前身份无权访问集群列表。", error_type="permission_denied")
        if response.status_code < 200 or response.status_code >= 300:
            return self._snapshot_unavailable(
                paths,
                f"DBAAS 集群接口返回异常状态：{response.status_code}",
                error_type="dbaas_request_failed",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            return self._snapshot_unavailable(paths, f"DBAAS 集群接口返回非 JSON 数据：{exc}", error_type="dbaas_request_failed")

        shape_error = _validate_cluster_payload_shape(payload)
        if shape_error is not None:
            return self._snapshot_unavailable(paths, shape_error, error_type="snapshot_unavailable")

        data_tmp_path: Path | None = None
        meta_tmp_path: Path | None = None
        now = utcnow()
        try:
            data_tmp_path, bytes_written = write_json_temp(paths.data_path, payload)
            meta = {
                "kind": CLUSTERS_KIND,
                "scope": ADMIN_SCOPE,
                "user": None,
                "version": 1,
                "data_path": str(paths.data_path),
                "meta_path": str(paths.meta_path),
                "status": "fresh",
                "synced_at": isoformat(now),
                "expires_at": isoformat(now + timedelta(seconds=self.config.cluster_snapshot_ttl_seconds)),
                "ttl_seconds": self.config.cluster_snapshot_ttl_seconds,
                "record_count": len(payload),
                "bytes": bytes_written,
                "source": "dbaas-server",
                "source_endpoint": CLUSTERS_ENDPOINT,
                "schema_version": schema_version(CLUSTERS_KIND, scope=ADMIN_SCOPE),
                "schema_path": str(schema_path(CLUSTERS_KIND, app_root=self.app_root, scope=ADMIN_SCOPE)),
                "last_refresh_status": "success",
                "last_error": None,
            }
            meta_tmp_path, _ = write_json_temp(paths.meta_path, meta)
            replace_file_atomic(data_tmp_path, paths.data_path)
            replace_file_atomic(meta_tmp_path, paths.meta_path)
            logger.info("dbaas clusters snapshot refreshed records=%s bytes=%s", meta["record_count"], bytes_written)
            return meta
        except Exception as exc:  # noqa: BLE001
            if data_tmp_path is not None:
                delete_if_exists(data_tmp_path)
            if meta_tmp_path is not None:
                delete_if_exists(meta_tmp_path)
            logger.exception("dbaas clusters snapshot write failed")
            return self._snapshot_unavailable(paths, f"写入 DBAAS 集群数据失败：{exc}", error_type="snapshot_unavailable")

    def _paths(self) -> DbaasSnapshotPaths:
        return self.workspace.paths(CLUSTERS_KIND, scope=ADMIN_SCOPE)

    def _is_snapshot_fresh(self, paths: DbaasSnapshotPaths, meta: dict[str, Any] | None) -> bool:
        return (
            meta is not None
            and is_meta_fresh(meta)
            and meta.get("kind") == CLUSTERS_KIND
            and meta.get("scope") == ADMIN_SCOPE
            and meta.get("user") is None
            and meta.get("data_path") == str(paths.data_path)
            and meta.get("schema_version") == schema_version(CLUSTERS_KIND, scope=ADMIN_SCOPE)
            and meta.get("schema_path") == str(schema_path(CLUSTERS_KIND, app_root=self.app_root, scope=ADMIN_SCOPE))
            and paths.data_path.exists()
            and paths.meta_path.exists()
        )

    def _snapshot(self, paths: DbaasSnapshotPaths, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            **meta,
            "kind": CLUSTERS_KIND,
            "scope": ADMIN_SCOPE,
            "user": None,
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
        now = utcnow()
        meta = {
            "kind": CLUSTERS_KIND,
            "scope": ADMIN_SCOPE,
            "user": None,
            "version": 1,
            "status": "error",
            "error_type": error_type,
            "data_path": None,
            "meta_path": str(paths.meta_path),
            "synced_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(seconds=self.config.cluster_snapshot_ttl_seconds)),
            "ttl_seconds": self.config.cluster_snapshot_ttl_seconds,
            "record_count": 0,
            "bytes": 0,
            "source": "dbaas-server",
            "source_endpoint": CLUSTERS_ENDPOINT,
            "schema_version": schema_version(CLUSTERS_KIND, scope=ADMIN_SCOPE),
            "schema_path": str(schema_path(CLUSTERS_KIND, app_root=self.app_root, scope=ADMIN_SCOPE)),
            "last_refresh_status": "error",
            "last_error": message,
            "message": message,
        }
        write_meta_atomic(paths.meta_path, meta)

    def _snapshot_unavailable(
        self,
        paths: DbaasSnapshotPaths,
        message: str,
        *,
        error_type: str = "snapshot_unavailable",
    ) -> dict[str, Any]:
        return {
            "kind": CLUSTERS_KIND,
            "scope": ADMIN_SCOPE,
            "user": None,
            "status": "error",
            "error_type": error_type,
            "data_path": None,
            "meta_path": str(paths.meta_path),
            "last_error": message,
            "message": f"当前没有可用的 DBAAS 集群数据视图，暂时无法获得准确数据：{message}",
        }


def _validate_cluster_payload_shape(payload: Any) -> str | None:
    if not isinstance(payload, list):
        return "DBAAS 集群接口返回结构不是数组。"
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return f"DBAAS 集群接口第 {index} 条记录不是对象。"
        field_error = _validate_cluster_record(index, item)
        if field_error is not None:
            return field_error
    return None


_STRING_FIELDS = {
    "id",
    "name",
    "siteId",
    "siteName",
    "areaId",
    "areaName",
    "haNetworkTag",
    "description",
    "createdAt",
    "createdBy",
    "createdByName",
}
_NULLABLE_STRING_FIELDS = {"updatedAt", "updatedBy", "updatedByName"}
_STRING_ARRAY_FIELDS = {
    "supportedCpuArchitectures",
    "supportedCpuArchitectureNames",
    "supportedSoftwareTypes",
    "supportedNetworkNames",
}
_BOOLEAN_FIELDS = {"enabled"}
_REQUIRED_FIELDS = _STRING_FIELDS | _NULLABLE_STRING_FIELDS | _STRING_ARRAY_FIELDS | _BOOLEAN_FIELDS


def _validate_cluster_record(index: int, item: dict[str, Any]) -> str | None:
    for field in sorted(_REQUIRED_FIELDS):
        if field not in item:
            return f"DBAAS 集群接口第 {index} 条记录缺少字段：{field}。"
    for field in item:
        if field not in _REQUIRED_FIELDS:
            return f"DBAAS 集群接口第 {index} 条记录包含未定义字段：{field}。"
    for field in _STRING_FIELDS:
        if not isinstance(item[field], str):
            return f"DBAAS 集群接口第 {index} 条记录字段 {field} 不是字符串。"
    for field in _NULLABLE_STRING_FIELDS:
        if item[field] is not None and not isinstance(item[field], str):
            return f"DBAAS 集群接口第 {index} 条记录字段 {field} 不是字符串或 null。"
    for field in _STRING_ARRAY_FIELDS:
        value = item[field]
        if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
            return f"DBAAS 集群接口第 {index} 条记录字段 {field} 不是字符串数组。"
    for field in _BOOLEAN_FIELDS:
        if not isinstance(item[field], bool):
            return f"DBAAS 集群接口第 {index} 条记录字段 {field} 不是 boolean。"
    return None

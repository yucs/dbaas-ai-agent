from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any
from urllib.parse import quote

import httpx

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .metric_catalog import get_metric_catalog_entry
from .metric_models import MetricSnapshotPaths
from .metric_sync import _identity_headers
from .metric_workspace import MetricWorkspace, MetricWorkspaceError
from .sync import delete_if_exists, isoformat, is_meta_fresh, read_meta, utcnow
from .workspace import replace_file_atomic, write_json_temp


logger = logging.getLogger(__name__)


def ensure_history_snapshot(
    config: DbaasConfig,
    identity: Identity,
    *,
    unit_name: str,
    metric_key: str,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    if start_ts >= end_ts:
        return _error(metric_key, "history_time_range_invalid", "start_ts 必须小于 end_ts。")
    now_ts = int(utcnow().timestamp())
    if end_ts > now_ts:
        return _error(metric_key, "history_time_range_invalid", "end_ts 不能晚于当前时间。")
    if identity.role != "admin" and not identity.user:
        return _error(metric_key, "permission_denied", "当前用户身份缺少可见范围，无法查询历史监控。")
    if get_metric_catalog_entry(metric_key, app_root=APP_ROOT) is None:
        return _error(metric_key, "metric_not_found", f"监控项不存在：{metric_key}")

    workspace = MetricWorkspace(config)
    try:
        paths = workspace.history_paths(
            unit_name=unit_name,
            metric_key=metric_key,
            start_ts=start_ts,
            end_ts=end_ts,
            identity=identity,
        )
    except MetricWorkspaceError as exc:
        return _error(metric_key, "metric_not_found", str(exc))

    fresh = _fresh_snapshot(paths)
    if fresh is not None:
        return fresh
    _delete_snapshot(paths)
    return _refresh_history(
        config,
        identity,
        unit_name=unit_name,
        metric_key=metric_key,
        start_ts=start_ts,
        end_ts=end_ts,
        paths=paths,
    )


def _refresh_history(
    config: DbaasConfig,
    identity: Identity,
    *,
    unit_name: str,
    metric_key: str,
    start_ts: int,
    end_ts: int,
    paths: MetricSnapshotPaths,
) -> dict[str, Any]:
    endpoint = f"/units/{quote(unit_name, safe='')}/metrics/history"
    url = f"{config.server_base_url}{endpoint}"
    params = {"metric_key": metric_key, "start_ts": start_ts, "end_ts": end_ts}
    try:
        with httpx.Client(timeout=config.request_timeout_seconds, trust_env=False) as client:
            response = client.get(url, params=params, headers=_identity_headers(identity))
    except httpx.HTTPError as exc:
        logger.exception("dbaas history metric request failed metric_key=%s unit_name=%s", metric_key, unit_name)
        return _error(metric_key, "dbaas_request_failed", f"请求 DBAAS 历史监控接口失败：{exc}")

    if response.status_code in {401, 403}:
        return _error(metric_key, "permission_denied", "当前用户无权访问该单元的历史监控。")
    if response.status_code == 404:
        return _error(metric_key, "resource_not_found", "目标单元不存在或当前用户无权访问。")
    if response.status_code < 200 or response.status_code >= 300:
        return _error(metric_key, "dbaas_request_failed", f"DBAAS 历史监控接口返回异常状态：{response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        return _error(metric_key, "dbaas_request_failed", f"DBAAS 历史监控接口返回非 JSON 数据：{exc}")
    if not isinstance(payload, list):
        return _error(metric_key, "dbaas_request_failed", "DBAAS 历史监控接口返回结构不是数组。")

    now = utcnow()
    data_tmp_path = None
    meta_tmp_path = None
    try:
        data_tmp_path, bytes_written = write_json_temp(paths.data_path, payload)
        meta = {
            "metric_key": metric_key,
            "unit_name": unit_name,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "scope": paths.scope,
            "user": paths.user,
            "status": "fresh",
            "data_path": str(paths.data_path),
            "meta_path": str(paths.meta_path),
            "synced_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(seconds=config.metric_snapshot_ttl_seconds)),
            "ttl_seconds": config.metric_snapshot_ttl_seconds,
            "record_count": len(payload),
            "bytes": bytes_written,
            "source": "dbaas-server",
            "source_endpoint": endpoint,
            "last_refresh_status": "success",
            "last_error": None,
        }
        meta_tmp_path, _ = write_json_temp(paths.meta_path, meta)
        replace_file_atomic(data_tmp_path, paths.data_path)
        replace_file_atomic(meta_tmp_path, paths.meta_path)
        return _snapshot(metric_key, paths, meta)
    except Exception as exc:  # noqa: BLE001
        if data_tmp_path is not None:
            delete_if_exists(data_tmp_path)
        if meta_tmp_path is not None:
            delete_if_exists(meta_tmp_path)
        _delete_snapshot(paths)
        logger.exception("dbaas history metric snapshot write failed metric_key=%s", metric_key)
        return _error(metric_key, "snapshot_unavailable", f"写入历史监控快照失败：{exc}")


def _fresh_snapshot(paths: MetricSnapshotPaths) -> dict[str, Any] | None:
    meta = read_meta(paths.meta_path)
    if (
        meta is not None
        and is_meta_fresh(meta)
        and meta.get("data_path") == str(paths.data_path)
        and paths.data_path.exists()
    ):
        return _snapshot(str(meta.get("metric_key", "")), paths, meta)
    return None


def _snapshot(metric_key: str, paths: MetricSnapshotPaths, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **meta,
        "metric_key": metric_key,
        "scope": paths.scope,
        "user": paths.user,
        "status": "fresh",
        "data_path": str(paths.data_path),
        "meta_path": str(paths.meta_path),
    }


def _delete_snapshot(paths: MetricSnapshotPaths) -> None:
    delete_if_exists(paths.data_path)
    delete_if_exists(paths.meta_path)


def _error(metric_key: str, error_type: str, message: str) -> dict[str, Any]:
    return {
        "metric_key": metric_key,
        "status": "error",
        "error_type": error_type,
        "data_path": None,
        "message": message,
    }

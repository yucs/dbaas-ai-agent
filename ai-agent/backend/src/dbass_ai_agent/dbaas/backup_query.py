from __future__ import annotations

import json
import subprocess
from typing import Any

from dbass_ai_agent.identity.models import Identity

from .backup_sync import DbaasBackupSynchronizer
from .config import DbaasConfig


def query_dbaas_backup_data(
    config: DbaasConfig,
    identity: Identity,
    *,
    jq_filter: str,
    max_preview_items: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    snapshot = DbaasBackupSynchronizer(config).ensure_snapshot(identity, refresh=refresh)
    if snapshot.get("status") != "fresh":
        return snapshot
    return _query_snapshot(
        config,
        snapshot,
        jq_filter=jq_filter,
        max_preview_items=max_preview_items,
        success_message="查询完成，结果来自当前用户可见 DBAAS 备份快照。",
    )


def _query_snapshot(
    config: DbaasConfig,
    snapshot: dict[str, Any],
    *,
    jq_filter: str,
    max_preview_items: int | None,
    success_message: str,
) -> dict[str, Any]:
    data_path = snapshot.get("data_path")
    if not isinstance(data_path, str) or not data_path:
        return {
            **snapshot,
            "status": "error",
            "error_type": "snapshot_unavailable",
            "message": "当前没有可用的备份快照文件路径。",
        }
    try:
        completed = subprocess.run(
            ["jq", "-c", jq_filter, data_path],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.jq_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            **snapshot,
            "status": "error",
            "error_type": "jq_timeout",
            "message": "jq 查询超时，请缩小查询条件。",
        }
    except FileNotFoundError:
        return {
            **snapshot,
            "status": "error",
            "error_type": "jq_not_found",
            "message": "系统未安装 jq，无法执行 DBAAS 备份查询。",
        }

    if completed.returncode != 0:
        return {
            **snapshot,
            "status": "error",
            "error_type": "jq_error",
            "jq_filter": jq_filter,
            "message": _safe_error(completed.stderr),
        }

    output = completed.stdout
    output_bytes = len(output.encode("utf-8"))
    byte_truncated = output_bytes > config.jq_max_output_bytes
    if byte_truncated:
        output = output.encode("utf-8")[: config.jq_max_output_bytes].decode("utf-8", errors="ignore")

    values = _parse_jq_output(output)
    preview_limit = _resolve_preview_limit(config, max_preview_items)
    preview, item_truncated = _preview_values(values, preview_limit)
    truncated = byte_truncated or item_truncated
    return {
        **snapshot,
        "status": "success",
        "jq_filter": jq_filter,
        "preview": preview,
        "preview_count": len(preview) if isinstance(preview, list) else 1,
        "truncated": truncated,
        "byte_truncated": byte_truncated,
        "message": "查询结果较大，仅返回预览，请缩小查询条件。" if truncated else success_message,
    }


def _resolve_preview_limit(config: DbaasConfig, requested: int | None) -> int:
    if requested is None:
        return config.jq_max_preview_items
    return max(1, min(requested, config.jq_max_preview_items))


def _parse_jq_output(output: str) -> list[Any]:
    stripped = output.strip()
    if not stripped:
        return []
    values: list[Any] = []
    for line in stripped.splitlines():
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError:
            values.append(line)
    return values


def _preview_values(values: list[Any], limit: int) -> tuple[Any, bool]:
    if len(values) == 1 and isinstance(values[0], list):
        array_value = values[0]
        return array_value[:limit], len(array_value) > limit
    if len(values) == 1 and not isinstance(values[0], dict):
        return values[0], False
    return values[:limit], len(values) > limit


def _safe_error(stderr: str) -> str:
    message = stderr.strip().splitlines()
    if not message:
        return "jq 表达式执行失败。"
    return message[-1][:500]

from __future__ import annotations

import json
import subprocess
from typing import Any

from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .constants import SERVICES_KIND, SUPPORTED_KINDS
from .sync import is_meta_fresh, read_meta
from .workspace import DbaasWorkspace


class DbaasQueryError(RuntimeError):
    """Raised when a DBAAS data query fails."""


def query_dbaas_data(
    config: DbaasConfig,
    identity: Identity,
    *,
    kind: str,
    jq_filter: str,
    max_preview_items: int | None = None,
) -> dict[str, Any]:
    if kind not in SUPPORTED_KINDS:
        return {
            "kind": kind,
            "status": "error",
            "error_type": "unsupported_kind",
            "message": f"暂不支持查询 DBAAS 数据类型：{kind}",
        }
    if kind != SERVICES_KIND:
        return {
            "kind": kind,
            "status": "error",
            "error_type": "unsupported_kind",
            "message": "第一版仅支持 services 查询。",
        }

    visible = _current_services_snapshot(config, identity)
    if visible.get("status") != "fresh":
        return visible

    data_path = visible.get("data_path")
    if not isinstance(data_path, str) or not data_path:
        return {
            "kind": kind,
            "status": "error",
            "error_type": "missing_data_path",
            "message": "当前用户可见快照路径不存在。",
        }

    preview_limit = _resolve_preview_limit(config, max_preview_items)
    try:
        command = _jq_command(identity, jq_filter, data_path)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.jq_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "kind": kind,
            "status": "error",
            "error_type": "jq_timeout",
            "message": "jq 查询超时，请缩小查询条件。",
        }
    except FileNotFoundError:
        return {
            "kind": kind,
            "status": "error",
            "error_type": "jq_not_found",
            "message": "系统未安装 jq，无法执行 DBAAS 数据查询。",
        }

    if completed.returncode != 0:
        return {
            "kind": kind,
            "status": "error",
            "error_type": "jq_error",
            "message": _safe_error(completed.stderr),
        }

    output = completed.stdout
    output_bytes = len(output.encode("utf-8"))
    byte_truncated = output_bytes > config.jq_max_output_bytes
    if byte_truncated:
        output = output.encode("utf-8")[: config.jq_max_output_bytes].decode("utf-8", errors="ignore")

    values = _parse_jq_output(output)
    preview, item_truncated = _preview_values(values, preview_limit)
    truncated = byte_truncated or item_truncated
    return {
        "kind": kind,
        "scope": visible.get("scope"),
        "status": "success",
        "jq_filter": jq_filter,
        "preview": preview,
        "preview_count": len(preview) if isinstance(preview, list) else 1,
        "truncated": truncated,
        "data_path": data_path,
        "message": (
            "查询结果较大，仅返回预览，请缩小查询条件。"
            if truncated
            else "查询完成，结果来自当前用户可见 DBAAS 数据。"
        ),
    }


def _current_services_snapshot(config: DbaasConfig, identity: Identity) -> dict[str, Any]:
    workspace = DbaasWorkspace(config)
    meta_path = workspace.meta_path(SERVICES_KIND)
    data_path = workspace.data_path(SERVICES_KIND)
    meta = read_meta(meta_path)
    if meta is None:
        return _snapshot_unavailable(config, "服务列表快照元数据不存在，后台同步可能尚未完成。")
    if not is_meta_fresh(meta):
        return _snapshot_unavailable(config, "服务列表快照已过期，后台同步可能尚未完成或拉取 DBAAS 数据失败。")
    if meta.get("data_path") != str(data_path):
        return _snapshot_unavailable(config, "服务列表快照元数据中的 data_path 与当前工作目录不一致。")
    if not data_path.exists():
        return _snapshot_unavailable(config, "服务列表快照文件不存在，后台同步可能尚未完成。")
    if identity.role != "admin" and not identity.user:
        return {
            "kind": SERVICES_KIND,
            "status": "error",
            "error_type": "permission_identity_missing",
            "data_path": None,
            "message": "当前用户身份缺少可见范围，无法查询 DBAAS 服务列表。",
        }
    return {
        **meta,
        "scope": "admin" if identity.role == "admin" else "user",
        "data_path": str(data_path),
    }


def _snapshot_unavailable(config: DbaasConfig, message: str) -> dict[str, Any]:
    workspace = DbaasWorkspace(config)
    return {
        "kind": SERVICES_KIND,
        "scope": "admin",
        "status": "error",
        "error_type": "snapshot_unavailable",
        "data_path": None,
        "meta_path": str(workspace.meta_path(SERVICES_KIND)),
        "message": f"当前没有可用的服务列表快照：{message}",
    }


def _jq_command(identity: Identity, jq_filter: str, data_path: str) -> list[str]:
    if identity.role == "admin":
        return ["jq", "-c", jq_filter, data_path]
    wrapped_filter = f"[.[] | select(.user == $current_user)] | ({jq_filter})"
    return ["jq", "--arg", "current_user", str(identity.user), "-c", wrapped_filter, data_path]


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

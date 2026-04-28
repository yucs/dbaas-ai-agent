from __future__ import annotations

import json
import subprocess
from typing import Any

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .constants import SERVICES_KIND, SUPPORTED_KINDS
from .visibility import ensure_visible_services


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

    visible = ensure_visible_services(config, identity, app_root=APP_ROOT)
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
        completed = subprocess.run(
            ["jq", "-c", jq_filter, data_path],
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

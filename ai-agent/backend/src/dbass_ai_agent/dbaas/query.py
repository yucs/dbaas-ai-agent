from __future__ import annotations

import json
import subprocess
from typing import Any

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .constants import ADMIN_SCOPE, SERVICES_KIND, SUPPORTED_KINDS, USER_SCOPE
from .schema import schema_path, schema_version
from .sync import DbaasServiceSynchronizer, is_meta_fresh, read_meta
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
    refresh: bool = False,
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

    synchronizer = DbaasServiceSynchronizer(config)
    if refresh:
        refreshed = _force_refresh_services_snapshot(
            synchronizer,
            identity,
            timeout_seconds=config.user_snapshot_refresh_wait_seconds,
        )
        if refreshed.get("status") != "fresh" or refreshed.get("last_refresh_status") != "success":
            return _refresh_failed_response(identity, refreshed)
        visible = _current_services_snapshot(config, identity)
    else:
        visible = _current_services_snapshot(config, identity)
        if (
            identity.role != ADMIN_SCOPE
            and visible.get("status") == "error"
            and visible.get("error_type") == "snapshot_missing"
            and identity.user
        ):
            synchronizer.force_refresh_user_services(
                identity,
                timeout_seconds=config.user_snapshot_refresh_wait_seconds,
            )
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
        **visible,
        "status": "success",
        "jq_filter": jq_filter,
        "preview": preview,
        "preview_count": len(preview) if isinstance(preview, list) else 1,
        "truncated": truncated,
        "byte_truncated": byte_truncated,
        "message": (
            "查询结果较大，仅返回预览，请缩小查询条件。"
            if truncated
            else "查询完成，结果来自当前用户可见 DBAAS 服务数据视图。"
        ),
    }


def _current_services_snapshot(config: DbaasConfig, identity: Identity) -> dict[str, Any]:
    if identity.role != ADMIN_SCOPE and not identity.user:
        return {
            "kind": SERVICES_KIND,
            "status": "error",
            "error_type": "permission_identity_missing",
            "data_path": None,
            "message": "当前用户身份缺少可见范围，无法查询 DBAAS 服务列表。",
        }
    workspace = DbaasWorkspace(config)
    scope = ADMIN_SCOPE if identity.role == ADMIN_SCOPE else USER_SCOPE
    paths = workspace.paths(SERVICES_KIND, scope=scope, user=identity.user)
    meta = read_meta(paths.meta_path)
    if meta is None:
        return _snapshot_unavailable(
            paths,
            (
                "服务列表快照元数据不存在，后台同步可能尚未完成。"
                if scope == ADMIN_SCOPE
                else "服务列表快照元数据不存在，后台同步或当前用户 prewarm 可能尚未完成。"
            ),
            error_type="snapshot_missing" if scope == USER_SCOPE else "snapshot_unavailable",
        )
    if meta.get("status") == "error":
        return _snapshot_unavailable(
            paths,
            str(meta.get("message") or meta.get("last_error") or "服务列表快照刷新失败。"),
        )
    if not is_meta_fresh(meta):
        return _snapshot_unavailable(paths, "服务列表快照已过期，等待后台刷新后再查询。")
    if meta.get("scope") != scope or meta.get("user") != paths.user:
        return _snapshot_unavailable(paths, "服务列表快照元数据中的身份 scope 与当前身份不一致。")
    if meta.get("schema_version") != schema_version(SERVICES_KIND, scope=scope):
        return _snapshot_unavailable(paths, "服务列表快照元数据中的 schema_version 与当前身份不一致。")
    if meta.get("schema_path") != str(schema_path(SERVICES_KIND, app_root=APP_ROOT, scope=scope)):
        return _snapshot_unavailable(paths, "服务列表快照元数据中的 schema_path 与当前身份不一致。")
    if meta.get("data_path") != str(paths.data_path):
        return _snapshot_unavailable(paths, "服务列表快照元数据中的 data_path 与当前工作目录不一致。")
    if not paths.data_path.exists():
        return _snapshot_unavailable(
            paths,
            (
                "服务列表快照文件不存在，后台同步可能尚未完成。"
                if scope == ADMIN_SCOPE
                else "服务列表快照文件不存在，后台同步或当前用户 prewarm 可能尚未完成。"
            ),
            error_type="snapshot_missing" if scope == USER_SCOPE else "snapshot_unavailable",
        )
    return {
        **meta,
        "scope": scope,
        "user": paths.user,
        "data_path": str(paths.data_path),
    }


def _snapshot_unavailable(paths, message: str, *, error_type: str = "snapshot_unavailable") -> dict[str, Any]:
    return {
        "kind": SERVICES_KIND,
        "scope": paths.scope,
        "user": paths.user,
        "status": "error",
        "error_type": error_type,
        "data_path": None,
        "meta_path": str(paths.meta_path),
        "message": f"当前没有可用的服务列表快照：{message}",
    }


def _force_refresh_services_snapshot(
    synchronizer: DbaasServiceSynchronizer,
    identity: Identity,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    if identity.role == ADMIN_SCOPE:
        return synchronizer.force_refresh_admin_services()
    if identity.user:
        return synchronizer.force_refresh_user_services(
            identity,
            timeout_seconds=timeout_seconds,
        )
    return {
        "kind": SERVICES_KIND,
        "scope": USER_SCOPE,
        "user": None,
        "status": "error",
        "error_type": "permission_identity_missing",
        "data_path": None,
        "message": "当前用户身份缺少可见范围，无法刷新 DBAAS 服务列表快照。",
    }


def _refresh_failed_response(identity: Identity, refreshed: dict[str, Any]) -> dict[str, Any]:
    scope = ADMIN_SCOPE if identity.role == ADMIN_SCOPE else USER_SCOPE
    return {
        "kind": SERVICES_KIND,
        "scope": refreshed.get("scope") or scope,
        "user": refreshed.get("user") if refreshed.get("user") is not None else identity.user,
        "status": "error",
        "error_type": str(refreshed.get("error_type") or "snapshot_unavailable"),
        "data_path": None,
        "meta_path": refreshed.get("meta_path"),
        "message": (
            f"当前无法刷新到最新的服务列表快照：{refreshed.get('last_error') or refreshed.get('message') or '刷新失败。'}"
        ),
    }


def _jq_command(identity: Identity, jq_filter: str, data_path: str) -> list[str]:
    return ["jq", "-c", jq_filter, data_path]


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

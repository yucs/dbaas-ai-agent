from __future__ import annotations

from typing import Any

from dbass_ai_agent.config import APP_ROOT
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .constants import ADMIN_SCOPE, SERVICES_KIND, USER_SCOPE
from .jq_query import query_snapshot_with_jq
from .schema import schema_path, schema_version
from .service_sync import DbaasServiceSynchronizer
from .snapshot_meta import is_meta_fresh, read_meta
from .workspace import DbaasWorkspace


def query_dbaas_service_data(
    config: DbaasConfig,
    identity: Identity,
    *,
    jq_filter: str,
    max_preview_items: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
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
        ):
            synchronizer.force_refresh_user_services(
                identity,
                timeout_seconds=config.user_snapshot_refresh_wait_seconds,
            )
            visible = _current_services_snapshot(config, identity)
    if visible.get("status") != "fresh":
        return visible

    return query_snapshot_with_jq(
        config,
        visible,
        jq_filter=jq_filter,
        max_preview_items=max_preview_items,
        success_message="查询完成，结果来自当前身份可见的 DBAAS 服务数据视图。",
        missing_data_path_message="当前身份可见的 DBAAS 服务数据路径不存在，暂时无法获得准确数据。",
        jq_not_found_message="系统未安装 jq，无法执行 DBAAS 数据查询。",
        missing_data_path_error_type="missing_data_path",
    )


def _current_services_snapshot(config: DbaasConfig, identity: Identity) -> dict[str, Any]:
    if identity.role != ADMIN_SCOPE and not identity.user_id:
        return {
            "kind": SERVICES_KIND,
            "status": "error",
            "error_type": "permission_identity_missing",
            "data_path": None,
            "message": "当前用户身份缺少可见范围，无法查询 DBAAS 服务列表。",
        }
    workspace = DbaasWorkspace(config)
    scope = ADMIN_SCOPE if identity.role == ADMIN_SCOPE else USER_SCOPE
    paths = workspace.paths(SERVICES_KIND, scope=scope, user=None if scope == ADMIN_SCOPE else identity.user_id)
    meta = read_meta(paths.meta_path)
    if meta is None:
        return _snapshot_unavailable(
            paths,
            (
                "DBAAS 服务数据视图元数据不存在，后台同步可能尚未完成。"
                if scope == ADMIN_SCOPE
                else "DBAAS 服务数据视图元数据不存在，后台同步或当前用户 prewarm 可能尚未完成。"
            ),
            error_type="snapshot_missing" if scope == USER_SCOPE else "snapshot_unavailable",
        )
    if meta.get("status") == "error":
        return _snapshot_unavailable(
            paths,
            str(meta.get("message") or meta.get("last_error") or "DBAAS 服务数据视图刷新失败。"),
        )
    if not is_meta_fresh(meta):
        return _snapshot_unavailable(paths, "DBAAS 服务数据视图已过期，等待后台刷新后再查询。")
    if meta.get("scope") != scope or meta.get("user") != paths.user:
        return _snapshot_unavailable(paths, "DBAAS 服务数据视图元数据中的身份 scope 与当前身份不一致。")
    if meta.get("schema_version") != schema_version(SERVICES_KIND, scope=scope):
        return _snapshot_unavailable(paths, "DBAAS 服务数据视图元数据中的 schema_version 与当前身份不一致。")
    if meta.get("schema_path") != str(schema_path(SERVICES_KIND, app_root=APP_ROOT, scope=scope)):
        return _snapshot_unavailable(paths, "DBAAS 服务数据视图元数据中的 schema_path 与当前身份不一致。")
    if meta.get("data_path") != str(paths.data_path):
        return _snapshot_unavailable(paths, "DBAAS 服务数据视图元数据中的 data_path 与当前工作目录不一致。")
    if not paths.data_path.exists():
        return _snapshot_unavailable(
            paths,
            (
                "DBAAS 服务数据文件不存在，后台同步可能尚未完成。"
                if scope == ADMIN_SCOPE
                else "DBAAS 服务数据文件不存在，后台同步或当前用户 prewarm 可能尚未完成。"
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
        "message": f"当前没有可用的 DBAAS 服务数据视图，暂时无法获得准确数据：{message}",
    }


def _force_refresh_services_snapshot(
    synchronizer: DbaasServiceSynchronizer,
    identity: Identity,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    if identity.role == ADMIN_SCOPE:
        return synchronizer.force_refresh_admin_services()
    if identity.user_id:
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
        "message": "当前用户身份缺少可见范围，无法刷新 DBAAS 服务数据视图。",
    }


def _refresh_failed_response(identity: Identity, refreshed: dict[str, Any]) -> dict[str, Any]:
    scope = ADMIN_SCOPE if identity.role == ADMIN_SCOPE else USER_SCOPE
    return {
        "kind": SERVICES_KIND,
        "scope": refreshed.get("scope") or scope,
        "user": refreshed.get("user") if refreshed.get("user") is not None else identity.user_id,
        "status": "error",
        "error_type": str(refreshed.get("error_type") or "snapshot_unavailable"),
        "data_path": None,
        "meta_path": refreshed.get("meta_path"),
        "message": (
            f"当前无法刷新 DBAAS 服务数据视图，暂时无法获得准确数据：{refreshed.get('last_error') or refreshed.get('message') or '刷新失败。'}"
        ),
    }

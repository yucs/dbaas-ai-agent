from __future__ import annotations

from typing import Any

from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .constants import ADMIN_SCOPE, HOSTS_KIND
from .host_sync import DbaasHostSynchronizer
from .jq_query import query_snapshot_with_jq


def query_dbaas_host_data(
    config: DbaasConfig,
    identity: Identity,
    *,
    jq_filter: str,
    max_preview_items: int | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    if identity.role != ADMIN_SCOPE:
        return _permission_denied()

    snapshot = DbaasHostSynchronizer(config).ensure_snapshot(identity, refresh=refresh)
    if snapshot.get("status") != "fresh":
        return snapshot

    return query_snapshot_with_jq(
        config,
        snapshot,
        jq_filter=jq_filter,
        max_preview_items=max_preview_items,
        success_message="查询完成，结果来自当前管理员可见的 DBAAS 主机数据视图。",
        missing_data_path_message="当前管理员可见的 DBAAS 主机数据路径不存在，暂时无法获得准确数据。",
        jq_not_found_message="系统未安装 jq，无法执行 DBAAS 主机数据查询。",
        missing_data_path_error_type="missing_data_path",
    )


def _permission_denied() -> dict[str, Any]:
    return {
        "kind": HOSTS_KIND,
        "scope": ADMIN_SCOPE,
        "user": None,
        "status": "error",
        "error_type": "permission_denied",
        "data_path": None,
        "meta_path": None,
        "last_error": "当前身份无权查询平台主机资产。",
        "message": "当前身份无权查询平台主机资产。主机资产查询需要管理员权限。",
    }

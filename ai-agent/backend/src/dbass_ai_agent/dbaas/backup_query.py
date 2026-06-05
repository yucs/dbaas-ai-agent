from __future__ import annotations

from typing import Any

from dbass_ai_agent.identity.models import Identity

from .backup_sync import DbaasBackupSynchronizer
from .config import DbaasConfig
from .jq_query import query_snapshot_with_jq


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
        success_message="查询完成，结果来自当前身份可见的 DBAAS 备份数据视图。",
    )


def _query_snapshot(
    config: DbaasConfig,
    snapshot: dict[str, Any],
    *,
    jq_filter: str,
    max_preview_items: int | None,
    success_message: str,
) -> dict[str, Any]:
    return query_snapshot_with_jq(
        config,
        snapshot,
        jq_filter=jq_filter,
        max_preview_items=max_preview_items,
        success_message=success_message,
        missing_data_path_message="当前没有可用的 DBAAS 备份数据路径，暂时无法获得准确数据。",
        jq_not_found_message="系统未安装 jq，无法执行 DBAAS 备份查询。",
    )

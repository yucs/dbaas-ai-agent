from __future__ import annotations

from typing import Any

from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .jq_query import query_snapshot_with_jq
from .metric_history import ensure_history_snapshot
from .metric_sync import ensure_latest_snapshot


def query_unit_latest_metric_data(
    config: DbaasConfig,
    identity: Identity,
    *,
    metric_key: str,
    jq_filter: str,
    max_preview_items: int | None = None,
) -> dict[str, Any]:
    snapshot = ensure_latest_snapshot(config, identity, metric_key)
    if snapshot.get("status") != "fresh":
        return snapshot
    return _query_snapshot(
        config,
        snapshot,
        jq_filter=jq_filter,
        max_preview_items=max_preview_items,
        success_message="查询完成，结果来自最新 DBAAS 监控数据视图。",
    )


def query_unit_metric_history(
    config: DbaasConfig,
    identity: Identity,
    *,
    unit_name: str,
    metric_key: str,
    start_ts: int,
    end_ts: int,
    jq_filter: str,
    max_preview_items: int | None = None,
) -> dict[str, Any]:
    snapshot = ensure_history_snapshot(
        config,
        identity,
        unit_name=unit_name,
        metric_key=metric_key,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if snapshot.get("status") != "fresh":
        return snapshot
    return _query_snapshot(
        config,
        snapshot,
        jq_filter=jq_filter,
        max_preview_items=max_preview_items,
        success_message="查询完成，结果来自 DBAAS 历史监控数据视图。",
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
        missing_data_path_message="当前没有可用的 DBAAS 监控数据路径，暂时无法获得准确数据。",
        jq_not_found_message="系统未安装 jq，无法执行 DBAAS 监控查询。",
    )

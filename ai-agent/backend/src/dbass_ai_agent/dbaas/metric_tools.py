from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity

from .config import dbaas_config_from_settings
from .metric_catalog import describe_unit_metric_catalog
from .metric_query import query_unit_latest_metric_data, query_unit_metric_history
from .time_tools import get_current_time


def build_metric_tools(settings: Settings, require_identity: Callable[[], Identity]) -> list[Any]:
    config = dbaas_config_from_settings(settings)

    @tool("describe_unit_metric_catalog_tool")
    def describe_unit_metric_catalog_tool(
        query: str,
        service_type: str | None = None,
        limit: int | None = 10,
    ) -> dict[str, Any]:
        """按关键词、服务类型或 metric_key 搜索 DBAAS 单元监控项 catalog。

        监控查询必须先通过该工具定位 metric_key、value_type、unit 和枚举语义。
        不要猜测 metric_key、监控值类型或异常枚举含义。
        """

        require_identity()
        return describe_unit_metric_catalog(query, service_type=service_type, limit=limit)

    @tool("query_unit_latest_metric_data_tool")
    def query_unit_latest_metric_data_tool(
        metric_key: str,
        jq_filter: str,
        max_preview_items: int | None = None,
    ) -> dict[str, Any]:
        """查询当前身份可见的 DBAAS latest 单元监控快照，并对快照执行 jq。

        metric_key 必须来自 describe_unit_metric_catalog_tool。
        指定服务、单元、类型、阈值等过滤条件都写入 jq_filter。
        """

        return query_unit_latest_metric_data(
            config,
            require_identity(),
            metric_key=metric_key,
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
        )

    @tool("query_unit_metric_history_tool")
    def query_unit_metric_history_tool(
        unit_name: str,
        metric_key: str,
        start_ts: int,
        end_ts: int,
        jq_filter: str,
        max_preview_items: int | None = None,
    ) -> dict[str, Any]:
        """查询指定真实单元的 DBAAS history 监控快照，并对历史点位数组执行 jq。"""

        return query_unit_metric_history(
            config,
            require_identity(),
            unit_name=unit_name,
            metric_key=metric_key,
            start_ts=start_ts,
            end_ts=end_ts,
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
        )

    @tool("get_current_time_tool")
    def get_current_time_tool() -> dict[str, Any]:
        """返回当前 Unix timestamp 秒数和 UTC/本地时间，用于换算相对 history 时间范围。"""

        require_identity()
        return get_current_time()

    return [
        describe_unit_metric_catalog_tool,
        query_unit_latest_metric_data_tool,
        query_unit_metric_history_tool,
        get_current_time_tool,
    ]

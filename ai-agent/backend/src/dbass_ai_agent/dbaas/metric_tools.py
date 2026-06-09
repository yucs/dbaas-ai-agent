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
        service_type: str,
        query: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """按服务类型列出或搜索 DBAAS 单元监控项 catalog。

        监控查询必须先通过该工具定位 metric_key、value_type、unit、枚举语义和 latest/history 数据结构。
        不要猜测 metric_key、监控值类型或异常枚举含义。service_type 必填；query 为空时列出该
        service_type 下可用的监控指标，query 非空时按关键词或 metric_key 搜索。
        service_type=container 表示所有单元通用的容器级指标；用户询问 mysql/redis 等服务的 CPU、
        内存、磁盘、网络资源时，应接受返回结果中的 container 指标，并在监控数据 jq 中按
        item.service_type 过滤目标服务类型。
        """

        require_identity()
        return describe_unit_metric_catalog(query, service_type=service_type, limit=limit)

    @tool("query_unit_latest_metric_data_tool")
    def query_unit_latest_metric_data_tool(
        metric_key: str,
        jq_filter: str,
        max_preview_items: int | None = None,
    ) -> dict[str, Any]:
        """查询当前身份可见的 DBAAS latest 单元监控数据视图，并执行 jq。

        metric_key 必须来自 describe_unit_metric_catalog_tool。
        latest 顶层是数组；jq_filter 可从 .[] 处理单个单元监控值。
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
        """查询指定真实单元的 DBAAS history 监控数据视图，并执行 jq。

        history 顶层直接是数组；jq_filter 应从 .[] 遍历历史点位，不存在 .data 包装层。
        每个历史点位通常包含 ts 和 value 字段。
        """

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
        """返回当前 Unix timestamp 秒数，以及 UTC、本地 ISO 时间和本地格式化日期时间。

        可用于相对时间换算和生成时间范围查询边界。
        """

        require_identity()
        return get_current_time()

    return [
        describe_unit_metric_catalog_tool,
        query_unit_latest_metric_data_tool,
        query_unit_metric_history_tool,
        get_current_time_tool,
    ]

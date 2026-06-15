from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity

from .cluster_query import query_dbaas_cluster_data
from .config import dbaas_config_from_settings


def build_cluster_tools(settings: Settings, require_identity: Callable[[], Identity]) -> list[Any]:
    config = dbaas_config_from_settings(settings)

    @tool("query_dbaas_cluster_data_tool")
    def query_dbaas_cluster_data_tool(
        jq_filter: str,
        max_preview_items: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """使用 jq 查询管理员可见的 DBAAS clusters 数据视图。

        如字段不确定、涉及支持的 CPU 架构/软件类型/网络或首次构造复杂 jq，先调用 describe_dbaas_schema_tool(kind="clusters")。
        clusters 顶层是数组；jq_filter 可从 .[] 处理单个集群。
        用户明确要求最新、当前、现在或刷新时，refresh 应传 true。
        """

        return query_dbaas_cluster_data(
            config,
            require_identity(),
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
            refresh=refresh,
        )

    return [query_dbaas_cluster_data_tool]

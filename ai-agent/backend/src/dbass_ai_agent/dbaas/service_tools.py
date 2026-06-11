from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import APP_ROOT, Settings
from dbass_ai_agent.identity.models import Identity

from .config import dbaas_config_from_settings
from .constants import SERVICES_KIND
from .schema import describe_schema
from .service_query import query_dbaas_service_data


def build_service_tools(settings: Settings, require_identity: Callable[[], Identity]) -> list[Any]:
    config = dbaas_config_from_settings(settings)

    @tool("query_dbaas_service_data_tool")
    def query_dbaas_service_data_tool(
        jq_filter: str,
        max_preview_items: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """使用 jq 查询当前身份可见的 DBAAS services 数据视图。

        如字段不确定或首次构造复杂 jq，先调用 describe_dbaas_schema_tool(kind="services")。
        services 顶层是数组；jq_filter 可从 .[] 处理单个服务。
        """

        return query_dbaas_service_data(
            config,
            require_identity(),
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
            refresh=refresh,
        )

    @tool("describe_dbaas_schema_tool")
    def describe_dbaas_schema_tool(kind: str = SERVICES_KIND) -> dict[str, Any]:
        """返回 DBAAS schema 完整内容。

        当前支持 kind=services、kind=backups 和管理员可见的 kind=hosts。
        用于确认 DBAAS 字段含义、可用字段、嵌套结构和 jq 查询路径。
        """

        return describe_schema(kind, app_root=APP_ROOT, identity=require_identity())

    return [query_dbaas_service_data_tool, describe_dbaas_schema_tool]

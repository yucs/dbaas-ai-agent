from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity

from .backup_query import query_dbaas_backup_data
from .config import dbaas_config_from_settings


def build_backup_tools(settings: Settings, require_identity: Callable[[], Identity]) -> list[Any]:
    config = dbaas_config_from_settings(settings)

    @tool("query_dbaas_backup_data_tool")
    def query_dbaas_backup_data_tool(
        jq_filter: str,
        max_preview_items: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """查询当前身份可见的 DBAAS 备份快照，并对快照执行 jq。

        生成 backups jq 前应先调用 describe_dbaas_schema_tool(kind="backups")。
        用户明确要求最新、刷新、当前或实时备份列表时传 refresh=true。
        """

        return query_dbaas_backup_data(
            config,
            require_identity(),
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
            refresh=refresh,
        )

    return [query_dbaas_backup_data_tool]

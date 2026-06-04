from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity

from .backup_query import query_dbaas_backup_data
from .config import dbaas_config_from_settings
from .write_client import DbaasWriteClient, DbaasWriteClientError


def build_backup_tools(settings: Settings, require_identity: Callable[[], Identity]) -> list[Any]:
    config = dbaas_config_from_settings(settings)
    client = DbaasWriteClient(config)

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

    @tool("describe_service_backup_capability_tool")
    def describe_service_backup_capability_tool(
        service_type: str | None = None,
        service_name: str | None = None,
        unit_name: str | None = None,
    ) -> dict[str, Any]:
        """查询 DBAAS 对指定服务类别、服务或 unit 的手动备份发起能力。

        发起备份前应先调用该工具确认 backup_type、retention_days、options 等参数字段。
        该工具只提供能力描述和运行提示，不做 backup precheck，也不会阻止写操作。
        """

        if not any([service_type, service_name, unit_name]):
            return {
                "status": "error",
                "error_type": "missing_target",
                "message": "至少需要提供 service_type、service_name 或 unit_name 之一。",
            }
        try:
            return client.describe_service_backup_capability(
                require_identity(),
                service_type=service_type,
                service_name=service_name,
                unit_name=unit_name,
            )
        except DbaasWriteClientError as exc:
            return {
                "status": "error",
                "error_type": exc.error_type,
                "message": str(exc),
                "status_code": exc.status_code,
            }

    return [query_dbaas_backup_data_tool, describe_service_backup_capability_tool]

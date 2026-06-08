from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity

from .config import dbaas_config_from_settings
from .write_client import DbaasWriteClient, DbaasWriteClientError


def build_capability_tools(settings: Settings, require_identity: Callable[[], Identity]) -> list[Any]:
    config = dbaas_config_from_settings(settings)
    client = DbaasWriteClient(config)

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

    @tool("describe_service_image_upgrade_capability_tool")
    def describe_service_image_upgrade_capability_tool(
        service_name: str,
        child_service_type: str,
    ) -> dict[str, Any]:
        """查询指定服务/子服务可升级的镜像和版本候选。

        发起镜像升级前应先调用该工具确认可选 image/version。
        如果用户没有明确指定 image/version，只能展示该工具返回的候选项供用户选择。
        """

        if not service_name.strip() or not child_service_type.strip():
            return {
                "status": "error",
                "error_type": "missing_target",
                "message": "必须提供 service_name 和 child_service_type。",
            }
        try:
            return client.describe_service_image_upgrade_capability(
                require_identity(),
                service_name,
                child_service_type=child_service_type,
            )
        except DbaasWriteClientError as exc:
            return {
                "status": "error",
                "error_type": exc.error_type,
                "message": str(exc),
                "status_code": exc.status_code,
            }

    return [describe_service_backup_capability_tool, describe_service_image_upgrade_capability_tool]

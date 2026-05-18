from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.dbaas.config import dbaas_config_from_settings
from dbass_ai_agent.dbaas.write_client import (
    DbaasWriteClient,
    DbaasWriteClientError,
)
from dbass_ai_agent.identity.models import Identity


def build_precheck_tools(settings: Settings, identity_getter: Callable[[], Identity]) -> list[Any]:
    dbaas_config = dbaas_config_from_settings(settings)
    client = DbaasWriteClient(dbaas_config)

    @tool("precheck_service_resource_update_tool")
    def precheck_service_resource_update_tool(
        service_name: str,
        child_service_type: str,
        target_cpu_cores: float | None = None,
        target_memory_gb: float | None = None,
    ) -> dict[str, Any]:
        """只读工具，用于服务 CPU/内存调整建议和执行前预检。

        用户询问是否需要扩容/缩容、推荐 CPU/内存目标规格、评估目标规格风险或执行资源调整前，调用本工具。
        本工具返回当前规格、可选规格、运行状态、unit 级 CPU/内存监控摘要和 blocking_errors，不执行写操作。
        """

        try:
            return client.precheck_service_resource_update(
                identity_getter(),
                service_name,
                child_service_type=child_service_type,
                target_cpu_cores=target_cpu_cores,
                target_memory_gb=target_memory_gb,
                timeout_seconds=dbaas_config.request_timeout_seconds,
            )
        except DbaasWriteClientError as exc:
            return _error_payload(exc)

    @tool("precheck_service_storage_update_tool")
    def precheck_service_storage_update_tool(
        service_name: str,
        child_service_type: str,
        target_data_volume_gb: float | None = None,
        target_log_volume_gb: float | None = None,
    ) -> dict[str, Any]:
        """只读工具，用于服务 data/log 卷容量调整建议和执行前预检。

        用户询问是否需要扩盘、推荐 data/log 目标容量、评估目标容量风险或执行存储调整前，调用本工具。
        本工具返回当前容量、运行状态、unit 级 data/log 最新使用率和 blocking_errors，不执行写操作。
        """

        try:
            return client.precheck_service_storage_update(
                identity_getter(),
                service_name,
                child_service_type=child_service_type,
                target_data_volume_gb=target_data_volume_gb,
                target_log_volume_gb=target_log_volume_gb,
                timeout_seconds=dbaas_config.request_timeout_seconds,
            )
        except DbaasWriteClientError as exc:
            return _error_payload(exc)

    return [
        precheck_service_resource_update_tool,
        precheck_service_storage_update_tool,
    ]


def _error_payload(exc: DbaasWriteClientError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": exc.error_type,
        "message": str(exc),
    }
    if exc.status_code is not None:
        payload["status_code"] = exc.status_code
    return payload

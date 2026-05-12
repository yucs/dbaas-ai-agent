from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import ToolCall

from .models import ExecutionMode, RequiredRole, RiskLevel


@dataclass(frozen=True, slots=True)
class ActionConfig:
    tool_name: str
    action: str
    execution_mode: ExecutionMode
    risk_level: RiskLevel
    required_role: RequiredRole
    timeout_seconds: int | None = None
    approval_ttl_seconds: int = 5 * 60
    risk_notes: tuple[str, ...] = ()


WRITE_ACTIONS: dict[str, ActionConfig] = {
    "update_service_resource_tool": ActionConfig(
        tool_name="update_service_resource_tool",
        action="service.resource.update",
        execution_mode="sync",
        risk_level="medium",
        required_role="user",
        timeout_seconds=30,
        risk_notes=("会变更该子服务下所有 unit 的资源规格。",),
    ),
    "update_service_storage_tool": ActionConfig(
        tool_name="update_service_storage_tool",
        action="service.storage.update",
        execution_mode="sync",
        risk_level="medium",
        required_role="user",
        timeout_seconds=45,
        risk_notes=("会变更该子服务下所有 unit 的存储规格。",),
    ),
    "create_service_image_upgrade_task_tool": ActionConfig(
        tool_name="create_service_image_upgrade_task_tool",
        action="service.image.upgrade",
        execution_mode="async",
        risk_level="high",
        required_role="user",
        timeout_seconds=30,
        risk_notes=("会创建镜像升级任务，任务完成前服务可能处于变更中。",),
    ),
}


def get_action_config(tool_name: str) -> ActionConfig | None:
    return WRITE_ACTIONS.get(tool_name)


def require_action_config(tool_name: str) -> ActionConfig:
    config = get_action_config(tool_name)
    if config is None:
        raise ValueError(f"unsupported write tool: {tool_name}")
    return config


def build_interrupt_on_config() -> dict[str, dict[str, Any]]:
    return {
        tool_name: {
            "allowed_decisions": ["approve", "reject"],
            "description": format_operation_approval,
        }
        for tool_name in WRITE_ACTIONS
    }


def format_operation_approval(tool_call: ToolCall, state: Any, runtime: Any) -> str:
    del state, runtime
    config = require_action_config(tool_call["name"])
    args = tool_call.get("args") or {}
    service_name = args.get("service_name") or args.get("name") or "-"
    child_type = args.get("child_service_type") or "-"
    return f"{config.action}: {service_name}/{child_type} 需要人工确认。"

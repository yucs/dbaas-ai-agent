from __future__ import annotations

from typing import Any

from .action_registry import require_action_config
from .models import OperationParameter, OperationProposal, OperationTarget


def build_operation_proposal(tool_name: str, tool_args: dict[str, Any]) -> OperationProposal:
    config = require_action_config(tool_name)
    target = _service_target(tool_args)
    return OperationProposal(
        action=config.action,
        targets=[target],
        summary=_summary(config.action, tool_args),
        risk_level=config.risk_level,
        required_role=config.required_role,
        execution_mode=config.execution_mode,
        parameters=_parameters(config.action, tool_args),
        risk_notes=list(config.risk_notes),
    )


def _service_target(tool_args: dict[str, Any]) -> OperationTarget:
    service_name = str(tool_args.get("service_name") or "")
    child_service_type = tool_args.get("child_service_type")
    qualifiers: dict[str, Any] = {}
    if child_service_type:
        qualifiers["child_service_type"] = child_service_type
    return OperationTarget(
        kind="service",
        id=service_name,
        name=service_name or None,
        qualifiers=qualifiers,
    )


def _summary(action: str, tool_args: dict[str, Any]) -> str:
    service_name = tool_args.get("service_name") or "-"
    child_type = tool_args.get("child_service_type") or "-"
    if action == "service.resource.update":
        changes = []
        if tool_args.get("cpu") is not None:
            changes.append(f"CPU 调整为 {tool_args['cpu']}C")
        if tool_args.get("memory") is not None:
            changes.append(f"内存调整为 {tool_args['memory']}GB")
        if tool_args.get("platform_auto") is not None:
            changes.append(f"平台自动分配设置为 {tool_args['platform_auto']}")
        return f"将 {service_name}/{child_type} " + "，".join(changes)
    if action == "service.storage.update":
        changes = []
        if tool_args.get("data_volume_size") is not None:
            changes.append(f"data 卷调整为 {tool_args['data_volume_size']}GB")
        if tool_args.get("log_volume_size") is not None:
            changes.append(f"log 卷调整为 {tool_args['log_volume_size']}GB")
        if tool_args.get("platform_auto") is not None:
            changes.append(f"平台自动分配设置为 {tool_args['platform_auto']}")
        return f"将 {service_name}/{child_type} " + "，".join(changes)
    if action == "service.image.upgrade":
        image = tool_args.get("image") or "-"
        version = tool_args.get("version")
        unit_ids = tool_args.get("unit_ids")
        scope = "全部 unit" if not unit_ids else f"指定 unit {unit_ids}"
        version_text = f"，版本 {version}" if version else ""
        return f"将 {service_name}/{child_type} 镜像升级为 {image}{version_text}，范围：{scope}"
    return f"执行 {action}"


def _parameters(action: str, tool_args: dict[str, Any]) -> list[OperationParameter]:
    parameters: list[OperationParameter] = []
    if action == "service.resource.update":
        _append_if_present(parameters, tool_args, "cpu", "CPU", "C")
        _append_if_present(parameters, tool_args, "memory", "内存", "GB")
        _append_if_present(parameters, tool_args, "platform_auto", "平台自动分配", None)
    elif action == "service.storage.update":
        _append_if_present(parameters, tool_args, "data_volume_size", "data 卷", "GB")
        _append_if_present(parameters, tool_args, "log_volume_size", "log 卷", "GB")
        _append_if_present(parameters, tool_args, "platform_auto", "平台自动分配", None)
    elif action == "service.image.upgrade":
        _append_if_present(parameters, tool_args, "image", "镜像", None)
        _append_if_present(parameters, tool_args, "version", "版本", None)
        _append_if_present(parameters, tool_args, "unit_ids", "升级 unit", None)
    return parameters


def _append_if_present(
    parameters: list[OperationParameter],
    tool_args: dict[str, Any],
    key: str,
    label: str,
    unit: str | None,
) -> None:
    if key not in tool_args or tool_args[key] is None:
        return
    parameters.append(
        OperationParameter(
            key=key,
            label=label,
            value=tool_args[key],
            unit=unit,
        )
    )

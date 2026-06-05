from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .action_registry import require_action_config
from .models import (
    ExecutionMode,
    OperationParameter,
    OperationProposal,
    OperationProposalItem,
    ProposalExecutionMode,
    RequiredRole,
    RiskLevel,
)
from .targets import InvalidOperationTargetError, target_from_tool_call


_RISK_ORDER: dict[RiskLevel, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}
_ROLE_ORDER: dict[RequiredRole, int] = {
    "user": 0,
    "admin": 1,
}


def build_operation_proposal(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    current_services: dict[str, dict[str, Any]] | None = None,
) -> OperationProposal:
    return build_batch_operation_proposal([(tool_name, tool_args)], current_services=current_services)


def build_batch_operation_proposal(
    tool_calls: list[tuple[str, dict[str, Any]]],
    *,
    current_services: dict[str, dict[str, Any]] | None = None,
) -> OperationProposal:
    items = [
        build_operation_proposal_item(
            tool_name,
            tool_args,
            current_service=(current_services or {}).get(str(tool_args.get("service_name") or "")),
        )
        for tool_name, tool_args in tool_calls
    ]
    if not items:
        raise ValueError("operation proposal must contain at least one item")
    return OperationProposal(
        summary=f"本次将执行 {len(items)} 个 DBAAS 变更操作",
        risk_level=_max_risk(item.risk_level for item in items),
        required_role=_max_role(item.required_role for item in items),
        execution_mode=_aggregate_execution_mode([item.execution_mode for item in items]),
        items=items,
    )


def build_operation_proposal_item(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    current_service: dict[str, Any] | None = None,
) -> OperationProposalItem:
    config = require_action_config(tool_name)
    try:
        target = target_from_tool_call(tool_name, tool_args)
    except InvalidOperationTargetError as exc:
        raise ValueError(str(exc)) from exc
    return OperationProposalItem(
        action=config.action,
        targets=[target],
        summary=_summary(config.action, tool_args),
        risk_level=config.risk_level,
        required_role=config.required_role,
        execution_mode=config.execution_mode,
        parameters=_parameters(config.action, tool_args, current_service=current_service),
        risk_notes=list(config.risk_notes),
    )


def _max_risk(values: Iterable[RiskLevel]) -> RiskLevel:
    risks = list(values)
    return max(risks, key=lambda value: _RISK_ORDER[value])


def _max_role(values: Iterable[RequiredRole]) -> RequiredRole:
    roles = list(values)
    return max(roles, key=lambda value: _ROLE_ORDER[value])


def _aggregate_execution_mode(values: list[ExecutionMode]) -> ProposalExecutionMode:
    modes = set(values)
    if len(modes) == 1:
        return values[0]
    return "mixed"


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
    if action == "service.backup.create":
        scope = tool_args.get("scope") or "service"
        backup_type = tool_args.get("backup_type") or "full"
        retention_days = tool_args.get("retention_days")
        target = tool_args.get("unit_name") or service_name
        retention_text = f"，保留 {retention_days} 天" if retention_days is not None else ""
        return f"为 {service_name}/{scope}/{target} 创建 {backup_type} 备份{retention_text}"
    return f"执行 {action}"


def _parameters(
    action: str,
    tool_args: dict[str, Any],
    *,
    current_service: dict[str, Any] | None,
) -> list[OperationParameter]:
    parameters: list[OperationParameter] = []
    child = _find_child(current_service or {}, str(tool_args.get("child_service_type") or ""))
    if action == "service.resource.update":
        _append_if_present(parameters, tool_args, "cpu", "CPU", "C", _first_unit_value(child, "cpu"), "C")
        _append_if_present(parameters, tool_args, "memory", "内存", "GB", _first_unit_value(child, "memory"), "GB")
        _append_if_present(
            parameters,
            tool_args,
            "platform_auto",
            "平台自动分配",
            None,
            child.get("platformAuto") if child else None,
            None,
        )
    elif action == "service.storage.update":
        _append_if_present(
            parameters,
            tool_args,
            "data_volume_size",
            "data 卷",
            "GB",
            _first_storage_size(child, "data"),
            "GB",
        )
        _append_if_present(
            parameters,
            tool_args,
            "log_volume_size",
            "log 卷",
            "GB",
            _first_storage_size(child, "log"),
            "GB",
        )
        _append_if_present(
            parameters,
            tool_args,
            "platform_auto",
            "平台自动分配",
            None,
            child.get("platformAuto") if child else None,
            None,
        )
    elif action == "service.image.upgrade":
        _append_if_present(parameters, tool_args, "image", "镜像", None)
        _append_if_present(parameters, tool_args, "version", "版本", None)
        _append_if_present(parameters, tool_args, "unit_ids", "升级 unit", None)
    elif action == "service.backup.create":
        _append_if_present(parameters, tool_args, "scope", "备份范围", None)
        _append_if_present(parameters, tool_args, "backup_type", "备份类型", None)
        _append_if_present(parameters, tool_args, "retention_days", "保留天数", "天")
        _append_if_present(parameters, tool_args, "unit_name", "Unit 名称", None)
        if tool_args.get("options"):
            _append_if_present(parameters, tool_args, "options", "备份参数", None)
        _append_if_present(parameters, tool_args, "remark", "备注", None)
    return parameters


def _append_if_present(
    parameters: list[OperationParameter],
    tool_args: dict[str, Any],
    key: str,
    label: str,
    unit: str | None,
    current_value: Any | None = None,
    current_unit: str | None = None,
) -> None:
    if key not in tool_args or tool_args[key] is None:
        return
    parameters.append(
        OperationParameter(
            key=key,
            label=label,
            value=tool_args[key],
            unit=unit,
            current_value=current_value,
            current_unit=current_unit,
        )
    )


def _find_child(service_detail: dict[str, Any], child_service_type: str) -> dict[str, Any]:
    for child in service_detail.get("services", []):
        if not isinstance(child, dict):
            continue
        if child.get("type") == child_service_type or child.get("name") == child_service_type:
            return child
    return {}


def _first_unit_value(child: dict[str, Any], field: str) -> Any:
    units = child.get("units")
    if not isinstance(units, list) or not units:
        return None
    first = units[0]
    return first.get(field) if isinstance(first, dict) else None


def _first_storage_size(child: dict[str, Any], storage_type: str) -> Any:
    units = child.get("units")
    if not isinstance(units, list) or not units:
        return None
    first = units[0]
    if not isinstance(first, dict):
        return None
    storage = first.get("storage")
    if not isinstance(storage, dict):
        return None
    volume = storage.get(storage_type)
    if not isinstance(volume, dict):
        return None
    return volume.get("size")

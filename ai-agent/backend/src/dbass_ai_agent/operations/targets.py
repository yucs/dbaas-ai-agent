from __future__ import annotations

from typing import Any

from .models import OperationTarget


def service_child_target(service_name: str, child_service_type: str | None = None) -> OperationTarget:
    qualifiers: dict[str, Any] = {}
    if child_service_type:
        qualifiers["child_service_type"] = child_service_type
    return OperationTarget(
        kind="service",
        id=service_name,
        name=service_name or None,
        qualifiers=qualifiers,
    )


def backup_target(
    service_name: str,
    *,
    scope: str = "service",
    unit_name: str | None = None,
) -> OperationTarget:
    normalized_scope = (scope or "service").strip().lower()
    if normalized_scope == "unit":
        target_id = unit_name or f"{service_name}/unit"
        qualifiers = {
            "scope": "unit",
            "service_name": service_name,
        }
        return OperationTarget(
            kind="unit",
            id=target_id,
            name=unit_name or None,
            qualifiers=qualifiers,
        )
    return OperationTarget(
        kind="service",
        id=service_name,
        name=service_name or None,
        qualifiers={"scope": "service"},
    )


def target_from_tool_call(tool_name: str, tool_args: dict[str, Any]) -> OperationTarget:
    service_name = str(tool_args.get("service_name") or "")
    if tool_name == "create_service_backup_task_tool":
        return backup_target(
            service_name,
            scope=str(tool_args.get("scope") or "service"),
            unit_name=_optional_str(tool_args.get("unit_name")),
        )
    return service_child_target(
        service_name,
        _optional_str(tool_args.get("child_service_type")),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

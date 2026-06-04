from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import Settings
from dbass_ai_agent.dbaas.config import dbaas_config_from_settings
from dbass_ai_agent.dbaas.write_client import (
    DbaasWriteClient,
    DbaasWriteClientError,
    DbaasWriteTimeout,
)
from dbass_ai_agent.operations.action_registry import require_action_config
from dbass_ai_agent.operations.context import require_operation_context
from dbass_ai_agent.operations.models import (
    OperationChange,
    OperationError,
    OperationResult,
    OperationTarget,
    OperationTaskRef,
)
from dbass_ai_agent.operations.operation_service import operation_result_payload
from dbass_ai_agent.operations.task_service import TaskConflictError, build_operation_conflict_key
from dbass_ai_agent.operations.targets import backup_target, service_child_target


def build_write_tools(settings: Settings) -> list[Any]:
    dbaas_config = dbaas_config_from_settings(settings)
    client = DbaasWriteClient(dbaas_config)

    @tool("update_service_resource_tool")
    def update_service_resource_tool(
        service_name: str,
        child_service_type: str,
        platform_auto: bool | None = None,
        cpu: float | None = None,
        memory: float | None = None,
    ) -> dict[str, Any]:
        """更新 DBAAS 服务子服务的 CPU、内存或平台自动分配设置。写操作必须先经过人工确认。"""

        tool_name = "update_service_resource_tool"
        config = require_action_config(tool_name)
        context = require_operation_context()
        _assert_required_role(context.identity.role, config.required_role)
        target = _service_target(service_name, child_service_type)
        tool_args = _compact_tool_args(
            service_name=service_name,
            child_service_type=child_service_type,
            platform_auto=platform_auto,
            cpu=cpu,
            memory=memory,
        )
        existing_operation = context.operation_service.find_existing(
            context.session,
            approval=context.approval,
            action=config.action,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )
        if existing_operation is not None:
            existing = context.operation_service.result_from_existing(existing_operation)
            if existing is not None:
                return operation_result_payload(existing)
            unknown = context.operation_service.mark_started_unknown(
                context.session,
                existing_operation,
                targets=[target],
            )
            return operation_result_payload(unknown.result)
        operation = context.operation_service.start_operation(
            context.session,
            approval=context.approval,
            run_id=context.run_id,
            action=config.action,
            execution_mode=config.execution_mode,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )

        try:
            before = client.get_service(
                context.identity,
                service_name,
                timeout_seconds=dbaas_config.request_timeout_seconds,
            )
            after = client.update_service_resource(
                context.identity,
                service_name,
                child_service_type=child_service_type,
                platform_auto=platform_auto,
                cpu=cpu,
                memory=memory,
                timeout_seconds=config.timeout_seconds,
            )
            changes = _resource_changes(target, before, after, child_service_type)
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=config.action,
                targets=[target],
                execution_mode="sync",
                status="succeeded",
                summary=f"已更新 {service_name}/{child_service_type} 的资源规格。",
                changes=changes,
                details={
                    "unit_count": _child_unit_count(after, child_service_type),
                    "before_snapshot": before,
                    "after_snapshot": after,
                },
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="succeeded",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteTimeout as exc:
            result = _timeout_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="timeout",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteClientError as exc:
            result = _failed_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="failed",
                result=result,
            )
            return operation_result_payload(result)

    @tool("update_service_storage_tool")
    def update_service_storage_tool(
        service_name: str,
        child_service_type: str,
        platform_auto: bool | None = None,
        data_volume_size: float | None = None,
        log_volume_size: float | None = None,
    ) -> dict[str, Any]:
        """更新 DBAAS 服务子服务的 data/log 存储规格。写操作必须先经过人工确认。"""

        tool_name = "update_service_storage_tool"
        config = require_action_config(tool_name)
        context = require_operation_context()
        _assert_required_role(context.identity.role, config.required_role)
        target = _service_target(service_name, child_service_type)
        tool_args = _compact_tool_args(
            service_name=service_name,
            child_service_type=child_service_type,
            platform_auto=platform_auto,
            data_volume_size=data_volume_size,
            log_volume_size=log_volume_size,
        )
        existing_operation = context.operation_service.find_existing(
            context.session,
            approval=context.approval,
            action=config.action,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )
        if existing_operation is not None:
            existing = context.operation_service.result_from_existing(existing_operation)
            if existing is not None:
                return operation_result_payload(existing)
            unknown = context.operation_service.mark_started_unknown(
                context.session,
                existing_operation,
                targets=[target],
            )
            return operation_result_payload(unknown.result)
        operation = context.operation_service.start_operation(
            context.session,
            approval=context.approval,
            run_id=context.run_id,
            action=config.action,
            execution_mode=config.execution_mode,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )

        try:
            before = client.get_service(
                context.identity,
                service_name,
                timeout_seconds=dbaas_config.request_timeout_seconds,
            )
            after = client.update_service_storage(
                context.identity,
                service_name,
                child_service_type=child_service_type,
                platform_auto=platform_auto,
                data_volume_size=data_volume_size,
                log_volume_size=log_volume_size,
                timeout_seconds=config.timeout_seconds,
            )
            changes = _storage_changes(target, before, after, child_service_type)
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=config.action,
                targets=[target],
                execution_mode="sync",
                status="succeeded",
                summary=f"已更新 {service_name}/{child_service_type} 的存储规格。",
                changes=changes,
                details={
                    "unit_count": _child_unit_count(after, child_service_type),
                    "before_snapshot": before,
                    "after_snapshot": after,
                },
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="succeeded",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteTimeout as exc:
            result = _timeout_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="timeout",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteClientError as exc:
            result = _failed_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="failed",
                result=result,
            )
            return operation_result_payload(result)

    @tool("create_service_image_upgrade_task_tool")
    def create_service_image_upgrade_task_tool(
        service_name: str,
        child_service_type: str,
        image: str,
        version: str | None = None,
        unit_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """创建 DBAAS 服务镜像升级异步任务。写操作必须先经过人工确认。"""

        tool_name = "create_service_image_upgrade_task_tool"
        config = require_action_config(tool_name)
        context = require_operation_context()
        _assert_required_role(context.identity.role, config.required_role)
        target = _service_target(service_name, child_service_type)
        tool_args = _compact_tool_args(
            service_name=service_name,
            child_service_type=child_service_type,
            image=image,
            version=version,
            unit_ids=unit_ids,
        )
        existing_operation = context.operation_service.find_existing(
            context.session,
            approval=context.approval,
            action=config.action,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )
        if existing_operation is not None:
            existing = context.operation_service.result_from_existing(existing_operation)
            if existing is not None:
                return operation_result_payload(existing)
            unknown = context.operation_service.mark_started_unknown(
                context.session,
                existing_operation,
                targets=[target],
            )
            return operation_result_payload(unknown.result)
        operation = context.operation_service.start_operation(
            context.session,
            approval=context.approval,
            run_id=context.run_id,
            action=config.action,
            execution_mode=config.execution_mode,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )

        try:
            conflict_key = build_operation_conflict_key(config.action, [target])
            context.task_service.ensure_no_conflicting_task(context.session, conflict_key)
            payload = client.create_service_image_upgrade_task(
                context.identity,
                service_name,
                child_service_type=child_service_type,
                image=image,
                version=version,
                unit_ids=unit_ids,
                timeout_seconds=config.timeout_seconds,
            )
            task_id = str(payload.get("taskId") or "")
            if not task_id:
                raise DbaasWriteClientError(
                    "DBAAS 控制面未返回 taskId。",
                    error_type="dbaas_invalid_response",
                )
            task = context.task_service.create_task_record(
                context.session,
                task_id=task_id,
                operation_id=operation.operation_id,
                action=config.action,
                targets=[target],
                dbaas_type=config.action,
                source_status="RUNNING",
                message="image upgrade task created",
            )
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=config.action,
                targets=[target],
                execution_mode="async",
                status="task_created",
                summary=f"已创建 {service_name}/{child_service_type} 镜像升级任务 {task_id}。",
                task=OperationTaskRef(
                    task_id=task.task_id,
                    type=task.dbaas_type,
                    status=task.status,
                ),
                details={"task": task.model_dump(mode="json")},
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="task_created",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteTimeout as exc:
            result = _timeout_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="timeout",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteClientError as exc:
            result = _failed_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="failed",
                result=result,
            )
            return operation_result_payload(result)
        except TaskConflictError as exc:
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=config.action,
                targets=[target],
                execution_mode="async",
                status="failed",
                summary="当前 Session 已存在同类未结束任务，未创建新的镜像升级任务。",
                error=OperationError(
                    error_type="task_conflict",
                    message=f"已有未结束任务：{exc.task.task_id}",
                ),
                details={"existing_task": exc.task.model_dump(mode="json")},
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="failed",
                result=result,
            )
            return operation_result_payload(result)

    @tool("create_service_backup_task_tool")
    def create_service_backup_task_tool(
        service_name: str,
        scope: str,
        backup_type: str,
        retention_days: int,
        unit_name: str | None = None,
        options: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> dict[str, Any]:
        """创建 DBAAS 服务手动备份异步任务。写操作必须先经过人工确认。"""

        tool_name = "create_service_backup_task_tool"
        config = require_action_config(tool_name)
        context = require_operation_context()
        _assert_required_role(context.identity.role, config.required_role)
        target = backup_target(
            service_name,
            scope=scope,
            unit_name=unit_name,
        )
        tool_args = _compact_tool_args(
            service_name=service_name,
            scope=scope,
            backup_type=backup_type,
            retention_days=retention_days,
            unit_name=unit_name,
            options=options,
            remark=remark,
        )
        existing_operation = context.operation_service.find_existing(
            context.session,
            approval=context.approval,
            action=config.action,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )
        if existing_operation is not None:
            existing = context.operation_service.result_from_existing(existing_operation)
            if existing is not None:
                return operation_result_payload(existing)
            unknown = context.operation_service.mark_started_unknown(
                context.session,
                existing_operation,
                targets=[target],
            )
            return operation_result_payload(unknown.result)
        operation = context.operation_service.start_operation(
            context.session,
            approval=context.approval,
            run_id=context.run_id,
            action=config.action,
            execution_mode=config.execution_mode,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=[target],
        )

        try:
            conflict_key = build_operation_conflict_key(config.action, [target])
            context.task_service.ensure_no_conflicting_task(context.session, conflict_key)
            payload = client.create_service_backup_task(
                context.identity,
                service_name,
                scope=scope,
                backup_type=backup_type,
                retention_days=retention_days,
                unit_name=unit_name,
                options=options,
                remark=remark,
                timeout_seconds=config.timeout_seconds,
            )
            task_id = str(payload.get("taskId") or "")
            if not task_id:
                raise DbaasWriteClientError(
                    "DBAAS 控制面未返回 taskId。",
                    error_type="dbaas_invalid_response",
                )
            task = context.task_service.create_task_record(
                context.session,
                task_id=task_id,
                operation_id=operation.operation_id,
                action=config.action,
                targets=[target],
                dbaas_type=config.action,
                source_status="RUNNING",
                message="backup task created",
            )
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=config.action,
                targets=[target],
                execution_mode="async",
                status="task_created",
                summary=f"已创建 {service_name} {_backup_scope_label(scope, unit_name)}备份任务 {task_id}。",
                task=OperationTaskRef(
                    task_id=task.task_id,
                    type=task.dbaas_type,
                    status=task.status,
                ),
                details={
                    "task": task.model_dump(mode="json"),
                    "backup_request": tool_args,
                    "dbaas_response": payload,
                },
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="task_created",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteTimeout as exc:
            result = _timeout_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="timeout",
                result=result,
            )
            return operation_result_payload(result)
        except DbaasWriteClientError as exc:
            result = _failed_result(
                operation.operation_id,
                operation.approval_id,
                config.action,
                config.execution_mode,
                [target],
                exc,
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="failed",
                result=result,
            )
            return operation_result_payload(result)
        except TaskConflictError as exc:
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=config.action,
                targets=[target],
                execution_mode="async",
                status="failed",
                summary="当前 Session 已存在同类未结束任务，未创建新的备份任务。",
                error=OperationError(
                    error_type="task_conflict",
                    message=f"已有未结束任务：{exc.task.task_id}",
                ),
                details={"existing_task": exc.task.model_dump(mode="json")},
            )
            context.operation_service.complete_operation(
                context.session,
                operation,
                status="failed",
                result=result,
            )
            return operation_result_payload(result)

    @tool("get_dbaas_task_tool")
    def get_dbaas_task_tool(task_id: str) -> dict[str, Any]:
        """查询当前 Session 已记录的 DBAAS 异步任务状态。"""

        context = require_operation_context()
        for task in context.task_service.list_tasks(context.session):
            if task.task_id == task_id:
                refreshed = context.task_service.refresh_task(
                    context.identity,
                    context.session,
                    task,
                    force=True,
                )
                return refreshed.model_dump(mode="json")
        return {
            "status": "error",
            "error_type": "task_not_in_current_session",
            "message": "当前 Session 没有记录该 task_id。",
        }

    @tool("list_current_session_tasks_tool")
    def list_current_session_tasks_tool(status: str | None = None) -> dict[str, Any]:
        """列出当前 Session 已记录的 DBAAS 异步任务；用户询问有哪些任务在跑或刚才任务状态时使用。"""

        context = require_operation_context()
        tasks = context.task_service.list_tasks_with_lazy_refresh(
            context.identity,
            context.session,
        )
        normalized_status = (status or "").strip().lower()
        if normalized_status:
            tasks = [task for task in tasks if task.status == normalized_status]
        return {
            "session_id": context.session.session_id,
            "count": len(tasks),
            "items": [task.model_dump(mode="json") for task in tasks],
        }

    return [
        update_service_resource_tool,
        update_service_storage_tool,
        create_service_image_upgrade_task_tool,
        create_service_backup_task_tool,
        get_dbaas_task_tool,
        list_current_session_tasks_tool,
    ]


def _assert_required_role(role: str, required_role: str) -> None:
    if required_role == "admin" and role != "admin":
        raise PermissionError("该写操作需要管理员身份。")


def _service_target(service_name: str, child_service_type: str) -> OperationTarget:
    return service_child_target(service_name, child_service_type)


def _backup_scope_label(
    scope: str,
    unit_name: str | None,
) -> str:
    if scope == "unit":
        return f"unit {unit_name or '-'} "
    return "服务级 "


def _compact_tool_args(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _resource_changes(
    target: OperationTarget,
    before: dict[str, Any],
    after: dict[str, Any],
    child_service_type: str,
) -> list[OperationChange]:
    before_child = _find_child(before, child_service_type)
    after_child = _find_child(after, child_service_type)
    changes: list[OperationChange] = []
    for field, label, unit in [
        ("cpu", "CPU", "C"),
        ("memory", "内存", "GB"),
    ]:
        before_value = _first_unit_value(before_child, field)
        after_value = _first_unit_value(after_child, field)
        if before_value != after_value:
            changes.append(
                OperationChange(
                    target=target,
                    field=field,
                    label=label,
                    before=before_value,
                    after=after_value,
                    unit=unit,
                    change_type=_change_type(before_value, after_value),
                )
            )
    before_auto = before_child.get("platformAuto") if before_child else None
    after_auto = after_child.get("platformAuto") if after_child else None
    if before_auto != after_auto:
        changes.append(
            OperationChange(
                target=target,
                field="platform_auto",
                label="平台自动分配",
                before=before_auto,
                after=after_auto,
            )
        )
    return changes


def _storage_changes(
    target: OperationTarget,
    before: dict[str, Any],
    after: dict[str, Any],
    child_service_type: str,
) -> list[OperationChange]:
    before_child = _find_child(before, child_service_type)
    after_child = _find_child(after, child_service_type)
    changes: list[OperationChange] = []
    for field, label in [
        ("data", "data 卷"),
        ("log", "log 卷"),
    ]:
        before_value = _first_storage_size(before_child, field)
        after_value = _first_storage_size(after_child, field)
        if before_value != after_value:
            changes.append(
                OperationChange(
                    target=target,
                    field=f"{field}_volume_size",
                    label=label,
                    before=before_value,
                    after=after_value,
                    unit="GB",
                    change_type=_change_type(before_value, after_value),
                )
            )
    before_auto = before_child.get("platformAuto") if before_child else None
    after_auto = after_child.get("platformAuto") if after_child else None
    if before_auto != after_auto:
        changes.append(
            OperationChange(
                target=target,
                field="platform_auto",
                label="平台自动分配",
                before=before_auto,
                after=after_auto,
            )
        )
    return changes


def _timeout_result(
    operation_id: str,
    approval_id: str | None,
    action: str,
    execution_mode: str,
    targets: list[OperationTarget],
    exc: DbaasWriteTimeout,
) -> OperationResult:
    return OperationResult(
        operation_id=operation_id,
        approval_id=approval_id,
        action=action,
        targets=targets,
        execution_mode=execution_mode,
        status="timeout",
        summary="DBAAS 写操作请求超时，当前无法确认是否已生效。",
        error=OperationError(
            error_type=exc.error_type,
            message=str(exc),
        ),
        details={
            "timeout_seconds": exc.timeout_seconds,
            "reconcile_required": True,
        },
    )


def _failed_result(
    operation_id: str,
    approval_id: str | None,
    action: str,
    execution_mode: str,
    targets: list[OperationTarget],
    exc: DbaasWriteClientError,
) -> OperationResult:
    return OperationResult(
        operation_id=operation_id,
        approval_id=approval_id,
        action=action,
        targets=targets,
        execution_mode=execution_mode,
        status="failed",
        summary="DBAAS 写操作执行失败。",
        error=OperationError(
            error_type=exc.error_type,
            message=str(exc),
        ),
        details={"status_code": exc.status_code},
    )


def _find_child(service_detail: dict[str, Any], child_service_type: str) -> dict[str, Any]:
    for child in service_detail.get("services", []):
        if not isinstance(child, dict):
            continue
        if child.get("type") == child_service_type or child.get("name") == child_service_type:
            return child
    return {}


def _child_unit_count(service_detail: dict[str, Any], child_service_type: str) -> int:
    child = _find_child(service_detail, child_service_type)
    units = child.get("units")
    return len(units) if isinstance(units, list) else 0


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


def _change_type(before: Any, after: Any) -> str | None:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        if after > before:
            return "increase"
        if after < before:
            return "decrease"
    return None

from __future__ import annotations

from typing import Any

from dbass_ai_agent.infra.clock import utc_now
from dbass_ai_agent.infra.ids import new_operation_id
from dbass_ai_agent.operations.models import (
    ExecutionMode,
    OperationError,
    OperationRecord,
    OperationResult,
    OperationStatus,
    OperationTarget,
)
from dbass_ai_agent.sessions.models import ApprovalRecord, SessionMeta
from dbass_ai_agent.sessions.repository import SessionRepository


class OperationService:
    def __init__(self, repository: SessionRepository) -> None:
        self.repository = repository

    def list_operations(self, session: SessionMeta) -> list[OperationRecord]:
        return self.repository.load_operations(session.user_id, session.session_id)

    def find_by_approval(
        self,
        session: SessionMeta,
        approval_id: str,
    ) -> OperationRecord | None:
        for operation in self.list_operations(session):
            if operation.approval_id == approval_id:
                return operation
        return None

    def find_all_by_approval(
        self,
        session: SessionMeta,
        approval_id: str,
    ) -> list[OperationRecord]:
        return [operation for operation in self.list_operations(session) if operation.approval_id == approval_id]

    def find_existing(
        self,
        session: SessionMeta,
        *,
        approval: ApprovalRecord | None,
        action: str,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        targets: list[OperationTarget] | None = None,
    ) -> OperationRecord | None:
        operations = self.list_operations(session)
        tool_call_id = _find_interrupted_tool_call_id(approval, tool_name, tool_args)
        if tool_call_id:
            for operation in operations:
                if operation.tool_call_id == tool_call_id:
                    return operation
        if approval is not None and targets:
            for operation in operations:
                if operation.approval_id != approval.approval_id or operation.action != action:
                    continue
                if _operation_targets_match(operation, targets):
                    return operation
            return None
        for operation in operations:
            if approval is None and operation.action == action and operation.status == "started":
                return operation
        return None

    def start_operation(
        self,
        session: SessionMeta,
        *,
        approval: ApprovalRecord | None,
        run_id: str | None,
        action: str,
        execution_mode: ExecutionMode,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        targets: list[OperationTarget] | None = None,
    ) -> OperationRecord:
        existing = self.find_existing(
            session,
            approval=approval,
            action=action,
            tool_name=tool_name,
            tool_args=tool_args,
            targets=targets,
        )
        if existing is not None:
            return existing

        now = utc_now()
        operation = OperationRecord(
            operation_id=new_operation_id(),
            approval_id=approval.approval_id if approval else None,
            session_id=session.session_id,
            thread_id=session.thread_id,
            run_id=run_id,
            tool_call_id=_find_interrupted_tool_call_id(approval, tool_name, tool_args),
            action=action,
            execution_mode=execution_mode,
            status="started",
            result=None,
            created_at=now,
            started_at=now,
            completed_at=None,
        )
        self.repository.append_operation(session.user_id, session.session_id, operation)
        return operation

    def complete_operation(
        self,
        session: SessionMeta,
        operation: OperationRecord,
        *,
        status: OperationStatus,
        result: OperationResult,
    ) -> OperationRecord:
        completed = operation.model_copy(
            update={
                "status": status,
                "result": result,
                "completed_at": utc_now(),
            }
        )
        self.repository.append_operation(session.user_id, session.session_id, completed)
        return completed

    def result_from_existing(self, operation: OperationRecord) -> OperationResult | None:
        if operation.result is not None:
            return operation.result
        return None

    def unknown_interrupted_result(
        self,
        operation: OperationRecord,
        *,
        targets: list[OperationTarget] | None = None,
    ) -> OperationResult:
        return OperationResult(
            operation_id=operation.operation_id,
            approval_id=operation.approval_id,
            action=operation.action,
            targets=targets or [],
            execution_mode=operation.execution_mode,
            status="unknown",
            summary="操作在 AI Agent 服务重启或上一次执行中断期间中断，当前无法确认是否已生效。",
            error=OperationError(
                error_type="operation_interrupted",
                message="AI Agent 在写操作完成前中断。",
            ),
            details={"reconcile_required": True},
        )

    def mark_started_unknown(
        self,
        session: SessionMeta,
        operation: OperationRecord,
        *,
        targets: list[OperationTarget] | None = None,
    ) -> OperationRecord:
        result = self.unknown_interrupted_result(operation, targets=targets)
        return self.complete_operation(
            session,
            operation,
            status="unknown",
            result=result,
        )


def operation_result_payload(result: OperationResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def _find_interrupted_tool_call_id(
    approval: ApprovalRecord | None,
    tool_name: str | None,
    tool_args: dict[str, Any] | None,
) -> str | None:
    if approval is None or not tool_name:
        return None
    normalized_args = _normalize_tool_args(tool_args or {})
    for tool_call in approval.interrupted_tool_calls:
        if tool_call.tool_name != tool_name:
            continue
        if _normalize_tool_args(tool_call.tool_args) == normalized_args:
            return tool_call.tool_call_id or None
    return None


def _normalize_tool_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in tool_args.items() if value is not None}


def _operation_targets_match(operation: OperationRecord, targets: list[OperationTarget]) -> bool:
    if operation.result is None:
        return False
    return _target_signature(operation.result.targets) == _target_signature(targets)


def _target_signature(targets: list[OperationTarget]) -> list[tuple[str, str, tuple[tuple[str, str], ...]]]:
    signature: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
    for target in targets:
        qualifiers = tuple(sorted((str(key), str(value)) for key, value in target.qualifiers.items()))
        signature.append((target.kind, target.id, qualifiers))
    return sorted(signature)

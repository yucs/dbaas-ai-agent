from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from dbass_ai_agent.agent.runtime import AgentInvocationError, DeepAgentRuntime
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.operations.approval_service import ApprovalService
from dbass_ai_agent.operations.operation_service import OperationService
from dbass_ai_agent.operations.task_service import TaskService

from .deps import (
    get_agent_runtime,
    get_agent_runtime_factory,
    get_approval_service,
    get_current_identity,
    get_operation_service,
    get_task_service,
)
from .schemas import ApprovalDecisionRequest, ApprovalDecisionResponse, ApprovalsResponse


router = APIRouter(prefix="/api/v1/sessions", tags=["approvals"])


@router.get("/{session_id}/approvals", response_model=ApprovalsResponse)
def get_approvals(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime_factory=Depends(get_agent_runtime_factory),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
) -> ApprovalsResponse:
    approval_service.expire_pending_approvals_for_query(
        identity,
        session_id,
        agent_runtime_factory=agent_runtime_factory,
        operation_service=operation_service,
        task_service=task_service,
    )
    return ApprovalsResponse(items=approval_service.get_approvals(identity, session_id))


@router.post("/{session_id}/approvals/{approval_id}/decision", response_model=ApprovalDecisionResponse)
def decide_approval(
    session_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    identity: Identity = Depends(get_current_identity),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime: DeepAgentRuntime = Depends(get_agent_runtime),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
) -> ApprovalDecisionResponse:
    decision = payload.decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="decision 仅支持 approved 或 rejected。",
        )
    try:
        result = approval_service.decide(
            identity,
            session_id,
            approval_id,
            decision,  # type: ignore[arg-type]
            agent_runtime=agent_runtime,
            operation_service=operation_service,
            task_service=task_service,
        )
    except AgentInvocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    return ApprovalDecisionResponse(
        approval=result.approval,
        assistant_message=result.assistant_message,
        system_message=result.system_message,
        operations=result.operations,
        tasks=result.tasks,
        next_approval=result.next_approval,
        paused=result.paused,
        run_id=result.reply.run_id if result.reply else None,
        mode=result.reply.mode if result.reply else None,
    )

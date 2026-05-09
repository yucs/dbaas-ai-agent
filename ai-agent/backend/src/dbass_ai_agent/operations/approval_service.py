from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from fastapi import HTTPException, status

from dbass_ai_agent.agent.runtime import AgentReply
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.clock import utc_now
from dbass_ai_agent.infra.ids import new_approval_id
from dbass_ai_agent.operations.action_registry import require_action_config
from dbass_ai_agent.operations.models import InterruptedToolCall, OperationRecord, TaskRecord
from dbass_ai_agent.operations.proposal_builder import build_operation_proposal
from dbass_ai_agent.sessions.models import ApprovalRecord, ChatMessage, SessionMeta
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.run_lock import session_locks
from dbass_ai_agent.sessions.service import SessionService


ApiDecision = Literal["approved", "rejected"]
USER_REJECTED_APPROVAL_MESSAGE = "用户已拒绝该操作，未执行 DBAAS 变更。"


@dataclass(frozen=True, slots=True)
class ApprovalInterrupt:
    action_request: dict[str, Any]
    review_config: dict[str, Any]
    tool_call_id: str
    interrupt_count: int = 1


@dataclass(frozen=True, slots=True)
class ApprovalDecisionResult:
    approval: ApprovalRecord
    assistant_message: ChatMessage | None
    operation: OperationRecord | None
    task: TaskRecord | None
    reply: AgentReply | None
    next_approval: ApprovalRecord | None = None
    paused: bool = False


class ApprovalService:
    def __init__(
        self,
        repository: SessionRepository,
        session_service: SessionService,
    ) -> None:
        self.repository = repository
        self.session_service = session_service

    def get_approvals(self, identity: Identity, session_id: str) -> list[ApprovalRecord]:
        detail = self.session_service.get_session(identity, session_id)
        return detail.approvals

    def create_approval(
        self,
        identity: Identity,
        session: SessionMeta,
        *,
        run_id: str,
        request_message_id: str,
        interrupt: ApprovalInterrupt,
    ) -> ApprovalRecord:
        self._assert_no_pending_approval(session)
        tool_name = str(interrupt.action_request.get("name") or "")
        tool_args = interrupt.action_request.get("args")
        if not isinstance(tool_args, dict):
            tool_args = {}
        config = require_action_config(tool_name)
        proposal = build_operation_proposal(tool_name, tool_args)
        self._assert_decision_role(identity, proposal.required_role)
        now = utc_now()
        approval = ApprovalRecord(
            approval_id=new_approval_id(),
            status="pending",
            action=proposal.action,
            session_id=session.session_id,
            thread_id=session.thread_id,
            run_id=run_id,
            request_message_id=request_message_id,
            proposal=proposal,
            interrupted_tool_call=InterruptedToolCall(
                tool_call_id=interrupt.tool_call_id,
                tool_name=tool_name,
                tool_args=tool_args,
            ),
            interrupt_count=interrupt.interrupt_count,
            allowed_decisions=["approve", "reject"],
            decided_by=None,
            created_at=now,
            expires_at=now + timedelta(seconds=config.approval_ttl_seconds),
            decided_at=None,
            expired_at=None,
            resume_failed=False,
            resume_error=None,
            resume_last_attempt_at=None,
        )
        self.repository.append_approval(session.user_id, session.session_id, approval)
        return approval

    def decide(
        self,
        identity: Identity,
        session_id: str,
        approval_id: str,
        decision: ApiDecision,
        *,
        agent_runtime: Any,
        operation_service: Any,
        task_service: Any,
    ) -> ApprovalDecisionResult:
        detail = self.session_service.get_session(identity, session_id)
        session = detail.meta
        approval = self._find_approval(session, approval_id)
        if approval.session_id and approval.session_id != session.session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审批记录不存在。")
        required_role = approval.proposal.required_role if approval.proposal else "user"
        self._assert_decision_role(identity, required_role)

        terminal = {"approved", "rejected", "expired"}
        if approval.status in terminal:
            if approval.status == decision:
                operation = operation_service.find_by_approval(session, approval.approval_id)
                task = self._find_task_for_operation(session, task_service, operation)
                if approval.resume_failed and operation is None:
                    return self._resume_terminal(
                        identity,
                        session,
                        approval,
                        decision,
                        agent_runtime=agent_runtime,
                        operation_service=operation_service,
                        task_service=task_service,
                    )
                return ApprovalDecisionResult(
                    approval=approval,
                    assistant_message=None,
                    operation=operation,
                    task=task,
                    reply=None,
                    next_approval=None,
                    paused=False,
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_type": "decision_conflict",
                    "detail": "审批已被其他决策处理，不能提交冲突决策。",
                },
            )

        if self._is_expired(approval):
            expired = self._mark_expired(session, approval)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_type": "approval_expired",
                    "detail": "审批已超时，操作已取消。",
                    "approval": expired.model_dump(mode="json"),
                },
            )

        with session_locks.acquire_run_lock(session.session_id) as acquired:
            if not acquired:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error_type": "session_run_locked",
                        "detail": "当前 Session 正在执行 AI 请求，请稍后再处理审批。",
                    },
                )
            latest = self._find_approval(session, approval_id)
            if latest.status != "pending":
                return self.decide(
                    identity,
                    session_id,
                    approval_id,
                    decision,
                    agent_runtime=agent_runtime,
                    operation_service=operation_service,
                    task_service=task_service,
                )
            decided = latest.model_copy(
                update={
                    "status": decision,
                    "decided_by": identity.user_id,
                    "decided_at": utc_now(),
                    "resume_failed": False,
                    "resume_error": None,
                    "resume_last_attempt_at": None,
                }
            )
            self.repository.append_approval(session.user_id, session.session_id, decided)
            return self._resume_decided(
                identity,
                session,
                decided,
                decision,
                agent_runtime=agent_runtime,
                operation_service=operation_service,
                task_service=task_service,
            )

    def expire_pending_approvals(
        self,
        identity: Identity,
        session_id: str,
        *,
        agent_runtime: Any | None = None,
        operation_service: Any | None = None,
        task_service: Any | None = None,
    ) -> list[ApprovalRecord]:
        detail = self.session_service.get_session(identity, session_id)
        expired: list[ApprovalRecord] = []
        for approval in detail.approvals:
            if approval.status != "pending" or not self._is_expired(approval):
                continue
            marked = self._mark_expired(detail.meta, approval)
            expired.append(marked)
            if agent_runtime and operation_service and task_service:
                try:
                    self._resume_decided(
                        identity,
                        detail.meta,
                        marked,
                        "rejected",
                        agent_runtime=agent_runtime,
                        operation_service=operation_service,
                        task_service=task_service,
                        reject_message="审批超时，操作已自动取消，未执行 DBAAS 变更。",
                    )
                except Exception:
                    continue
        return expired

    def has_pending_approval(self, session: SessionMeta) -> bool:
        return any(
            approval.status == "pending"
            for approval in self.repository.load_approvals(session.user_id, session.session_id)
        )

    def _resume_terminal(
        self,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        decision: ApiDecision,
        *,
        agent_runtime: Any,
        operation_service: Any,
        task_service: Any,
    ) -> ApprovalDecisionResult:
        with session_locks.acquire_run_lock(session.session_id) as acquired:
            if not acquired:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error_type": "session_run_locked",
                        "detail": "当前 Session 正在执行 AI 请求，请稍后重试恢复审批。",
                    },
                )
            return self._resume_decided(
                identity,
                session,
                approval,
                decision,
                agent_runtime=agent_runtime,
                operation_service=operation_service,
                task_service=task_service,
            )

    def _resume_decided(
        self,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        decision: ApiDecision,
        *,
        agent_runtime: Any,
        operation_service: Any,
        task_service: Any,
        reject_message: str | None = None,
    ) -> ApprovalDecisionResult:
        try:
            reply = agent_runtime.resume_approval(
                identity=identity,
                session=session,
                approval=approval,
                decision=decision,
                operation_service=operation_service,
                task_service=task_service,
                reject_message=reject_message,
            )
        except Exception as exc:
            failed = approval.model_copy(
                update={
                    "resume_failed": True,
                    "resume_error": str(exc),
                    "resume_last_attempt_at": utc_now(),
                }
            )
            self.repository.append_approval(session.user_id, session.session_id, failed)
            raise

        cleared = approval.model_copy(
            update={
                "resume_failed": False,
                "resume_error": None,
                "resume_last_attempt_at": utc_now(),
            }
        )
        if approval.resume_failed:
            self.repository.append_approval(session.user_id, session.session_id, cleared)
        assistant_content = _decision_assistant_content(
            reply.content,
            decision=decision,
            reject_message=reject_message,
        )
        assistant_message = None
        if assistant_content:
            assistant_message = self.session_service.append_assistant_message(
                identity,
                session.session_id,
                assistant_content,
            )
        operation = operation_service.find_by_approval(session, approval.approval_id)
        task = self._find_task_for_operation(session, task_service, operation)
        next_approval = None
        if reply.approval_request is not None:
            next_approval = self.create_approval(
                identity,
                session,
                run_id=reply.run_id,
                request_message_id=approval.request_message_id or "",
                interrupt=_approval_interrupt_from_runtime(reply.approval_request),
            )
        return ApprovalDecisionResult(
            approval=cleared,
            assistant_message=assistant_message,
            operation=operation,
            task=task,
            reply=reply,
            next_approval=next_approval,
            paused=next_approval is not None,
        )

    def _mark_expired(self, session: SessionMeta, approval: ApprovalRecord) -> ApprovalRecord:
        expired = approval.model_copy(
            update={
                "status": "expired",
                "expired_at": utc_now(),
            }
        )
        self.repository.append_approval(session.user_id, session.session_id, expired)
        return expired

    def _find_approval(self, session: SessionMeta, approval_id: str) -> ApprovalRecord:
        for approval in self.repository.load_approvals(session.user_id, session.session_id):
            if approval.approval_id == approval_id:
                return approval
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审批记录不存在。")

    def _assert_no_pending_approval(self, session: SessionMeta) -> None:
        if self.has_pending_approval(session):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_type": "session_has_pending_approval",
                    "detail": "当前 Session 已有待确认操作，请先处理后再发起新的操作。",
                },
            )

    @staticmethod
    def _assert_decision_role(identity: Identity, required_role: str) -> None:
        if required_role == "admin" and identity.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该操作需要管理员审批。",
            )

    @staticmethod
    def _is_expired(approval: ApprovalRecord) -> bool:
        return approval.expires_at is not None and approval.expires_at <= utc_now()

    @staticmethod
    def _find_task_for_operation(
        session: SessionMeta,
        task_service: Any,
        operation: OperationRecord | None,
    ) -> TaskRecord | None:
        if operation is None:
            return None
        for task in task_service.list_tasks(session):
            if task.operation_id == operation.operation_id:
                return task
        return None


def _approval_interrupt_from_runtime(value: Any) -> ApprovalInterrupt:
    action_request = getattr(value, "action_request", None)
    review_config = getattr(value, "review_config", None)
    tool_call_id = getattr(value, "tool_call_id", "")
    interrupt_count = getattr(value, "interrupt_count", 1)
    if not isinstance(action_request, dict) or not isinstance(review_config, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DeepAgent 审批暂停信息格式无效。",
        )
    return ApprovalInterrupt(
        action_request=action_request,
        review_config=review_config,
        tool_call_id=str(tool_call_id or ""),
        interrupt_count=max(1, int(interrupt_count or 1)),
    )


def _decision_assistant_content(
    content: str,
    *,
    decision: ApiDecision,
    reject_message: str | None,
) -> str:
    if decision == "rejected" and reject_message is None:
        return USER_REJECTED_APPROVAL_MESSAGE
    return content.strip()

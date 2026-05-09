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
from dbass_ai_agent.operations.proposal_builder import build_batch_operation_proposal
from dbass_ai_agent.sessions.models import ApprovalRecord, ChatMessage, SessionMeta
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.run_lock import session_locks
from dbass_ai_agent.sessions.service import SessionService


ApiDecision = Literal["approved", "rejected"]
USER_REJECTED_APPROVAL_MESSAGE = "用户已拒绝该操作，未执行 DBAAS 变更。"


@dataclass(frozen=True, slots=True)
class ApprovalInterrupt:
    action_requests: list[dict[str, Any]]
    review_configs: list[dict[str, Any]]
    tool_call_ids: list[str]


@dataclass(frozen=True, slots=True)
class ApprovalDecisionResult:
    approval: ApprovalRecord
    assistant_message: ChatMessage | None
    operations: list[OperationRecord]
    tasks: list[TaskRecord]
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
        if not interrupt.action_requests:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DeepAgent 审批暂停信息缺少待确认工具调用。",
            )
        if len(interrupt.action_requests) != len(interrupt.review_configs):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DeepAgent 审批暂停信息中 action_requests 与 review_configs 数量不一致。",
            )
        tool_calls: list[tuple[str, dict[str, Any]]] = []
        interrupted_tool_calls: list[InterruptedToolCall] = []
        ttl_seconds: int | None = None
        for index, action_request in enumerate(interrupt.action_requests):
            tool_name = str(action_request.get("name") or "")
            tool_args = action_request.get("args")
            if not isinstance(tool_args, dict):
                tool_args = {}
            config = require_action_config(tool_name)
            ttl_seconds = (
                config.approval_ttl_seconds
                if ttl_seconds is None
                else min(ttl_seconds, config.approval_ttl_seconds)
            )
            tool_calls.append((tool_name, tool_args))
            interrupted_tool_calls.append(
                InterruptedToolCall(
                    tool_call_id=interrupt.tool_call_ids[index] if index < len(interrupt.tool_call_ids) else "",
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
            )
        proposal = build_batch_operation_proposal(tool_calls)
        self._assert_decision_role(identity, proposal.required_role)
        now = utc_now()
        approval = ApprovalRecord(
            approval_id=new_approval_id(),
            status="pending",
            action=proposal.items[0].action if len(proposal.items) == 1 else "batch",
            session_id=session.session_id,
            thread_id=session.thread_id,
            run_id=run_id,
            request_message_id=request_message_id,
            proposal=proposal,
            interrupted_tool_calls=interrupted_tool_calls,
            allowed_decisions=["approve", "reject"],
            decided_by=None,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds or 30 * 60),
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
                operations = operation_service.find_all_by_approval(session, approval.approval_id)
                tasks = self._find_tasks_for_operations(session, task_service, operations)
                if approval.resume_failed and not operations:
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
                    operations=operations,
                    tasks=tasks,
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
        operations = operation_service.find_all_by_approval(session, approval.approval_id)
        tasks = self._find_tasks_for_operations(session, task_service, operations)
        next_approval = None
        if reply.approval_request is not None:
            next_approval = self.create_approval(
                identity,
                session,
                run_id=reply.run_id,
                request_message_id=approval.request_message_id or "",
                interrupt=approval_interrupt_from_runtime(reply.approval_request),
            )
        return ApprovalDecisionResult(
            approval=cleared,
            assistant_message=assistant_message,
            operations=operations,
            tasks=tasks,
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
    def _find_tasks_for_operations(
        session: SessionMeta,
        task_service: Any,
        operations: list[OperationRecord],
    ) -> list[TaskRecord]:
        operation_ids = {operation.operation_id for operation in operations}
        return [task for task in task_service.list_tasks(session) if task.operation_id in operation_ids]


def approval_interrupt_from_runtime(value: Any) -> ApprovalInterrupt:
    action_requests = getattr(value, "action_requests", None)
    review_configs = getattr(value, "review_configs", None)
    tool_call_ids = getattr(value, "tool_call_ids", None)
    if not isinstance(action_requests, list) or not action_requests:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DeepAgent 审批暂停信息格式无效。",
        )
    if not isinstance(review_configs, list) or not review_configs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DeepAgent 审批暂停信息格式无效。",
        )
    if len(action_requests) != len(review_configs):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DeepAgent 审批暂停信息中 action_requests 与 review_configs 数量不一致。",
        )
    normalized_action_requests: list[dict[str, Any]] = []
    normalized_review_configs: list[dict[str, Any]] = []
    for action_request, review_config in zip(action_requests, review_configs, strict=True):
        if not isinstance(action_request, dict) or not isinstance(review_config, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DeepAgent 审批暂停信息格式无效。",
            )
        normalized_action_requests.append(action_request)
        normalized_review_configs.append(review_config)
    normalized_tool_call_ids = [
        str(tool_call_id or "")
        for tool_call_id in (tool_call_ids if isinstance(tool_call_ids, list) else [])
    ]
    return ApprovalInterrupt(
        action_requests=normalized_action_requests,
        review_configs=normalized_review_configs,
        tool_call_ids=normalized_tool_call_ids,
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

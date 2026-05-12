from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from fastapi import HTTPException, status

from dbass_ai_agent.agent.runtime import AgentReply
from dbass_ai_agent.dbaas.write_client import DbaasWriteClientError
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.clock import utc_now
from dbass_ai_agent.infra.ids import new_approval_id
from dbass_ai_agent.operations.action_registry import require_action_config
from dbass_ai_agent.operations.models import InterruptedToolCall, OperationRecord, OperationTarget, TaskRecord
from dbass_ai_agent.operations.proposal_builder import build_batch_operation_proposal
from dbass_ai_agent.operations.task_service import TaskConflictError, build_operation_conflict_key
from dbass_ai_agent.sessions.models import ApprovalRecord, ChatMessage, SessionMeta
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.run_lock import session_locks
from dbass_ai_agent.sessions.service import SessionService


ApiDecision = Literal["approved", "rejected"]
USER_REJECTED_APPROVAL_MESSAGE = "用户已拒绝该操作，未执行 DBAAS 变更。"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApprovalInterrupt:
    action_requests: list[dict[str, Any]]
    review_configs: list[dict[str, Any]]
    tool_call_ids: list[str]


@dataclass(frozen=True, slots=True)
class ApprovalDecisionResult:
    approval: ApprovalRecord
    assistant_message: ChatMessage | None
    system_message: ChatMessage | None
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
        current_value_client: Any | None = None,
        current_value_timeout_seconds: int | None = None,
    ) -> None:
        self.repository = repository
        self.session_service = session_service
        self.current_value_client = current_value_client
        self.current_value_timeout_seconds = current_value_timeout_seconds

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
        proposal = build_batch_operation_proposal(
            tool_calls,
            current_services=self._load_current_services(identity, tool_calls),
        )
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
            expires_at=now + timedelta(seconds=ttl_seconds or 5 * 60),
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

        if approval.status == "expired":
            if approval.resume_failed:
                approval = self._resume_expired_cleanup_with_lock(
                    identity,
                    session,
                    approval,
                    agent_runtime=agent_runtime,
                    operation_service=operation_service,
                    task_service=task_service,
                )
            self._raise_approval_expired(approval)

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
                result_approval, system_message = self._emit_task_creation_notice_if_needed(
                    identity,
                    session,
                    approval,
                    decision=decision,
                    tasks=tasks,
                )
                return ApprovalDecisionResult(
                    approval=result_approval,
                    assistant_message=None,
                    system_message=system_message,
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
            if latest.status == "expired":
                if latest.resume_failed:
                    latest = self._resume_expired_cleanup(
                        identity,
                        session,
                        latest,
                        agent_runtime=agent_runtime,
                        operation_service=operation_service,
                        task_service=task_service,
                    )
                self._raise_approval_expired(latest)
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
            if self._is_expired(latest):
                expired = self._expire_and_resume(
                    identity,
                    session,
                    latest,
                    agent_runtime=agent_runtime,
                    operation_service=operation_service,
                    task_service=task_service,
                )
                self._raise_approval_expired(expired)
            if decision == "approved":
                self._assert_no_task_conflicts(session, latest, task_service)
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
            if approval.status == "pending" and self._is_expired(approval):
                if agent_runtime and operation_service and task_service:
                    marked = self._expire_and_resume(
                        identity,
                        detail.meta,
                        approval,
                        agent_runtime=agent_runtime,
                        operation_service=operation_service,
                        task_service=task_service,
                    )
                else:
                    marked = self._mark_expired(detail.meta, approval)
                    logger.info(
                        "approval expired approval_id=%s session_id=%s",
                        marked.approval_id,
                        detail.meta.session_id,
                    )
                expired.append(marked)
                continue
            if approval.status == "expired" and approval.resume_failed:
                marked = approval
            else:
                continue
            if agent_runtime and operation_service and task_service:
                self._resume_expired_cleanup(
                    identity,
                    detail.meta,
                    marked,
                    agent_runtime=agent_runtime,
                    operation_service=operation_service,
                    task_service=task_service,
                )
        return expired

    def has_failed_expired_resume(self, session: SessionMeta) -> bool:
        return any(
            approval.status == "expired" and approval.resume_failed
            for approval in self.repository.load_approvals(session.user_id, session.session_id)
        )

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
        persist_assistant_message: bool = True,
        allow_next_approval: bool = True,
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
        if assistant_content and persist_assistant_message:
            assistant_message = self.session_service.append_assistant_message(
                identity,
                session.session_id,
                assistant_content,
            )
        operations = operation_service.find_all_by_approval(session, approval.approval_id)
        tasks = self._find_tasks_for_operations(session, task_service, operations)
        result_approval, system_message = self._emit_task_creation_notice_if_needed(
            identity,
            session,
            cleared,
            decision=decision,
            tasks=tasks,
        )
        next_approval = None
        if reply.approval_request is not None and allow_next_approval:
            next_approval = self.create_approval(
                identity,
                session,
                run_id=reply.run_id,
                request_message_id=approval.request_message_id or "",
                interrupt=approval_interrupt_from_runtime(reply.approval_request),
            )
        return ApprovalDecisionResult(
            approval=result_approval,
            assistant_message=assistant_message,
            system_message=system_message,
            operations=operations,
            tasks=tasks,
            reply=reply,
            next_approval=next_approval,
            paused=next_approval is not None,
        )

    def _resume_expired_cleanup_with_lock(
        self,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        *,
        agent_runtime: Any,
        operation_service: Any,
        task_service: Any,
    ) -> ApprovalRecord:
        with session_locks.acquire_run_lock(session.session_id) as acquired:
            if not acquired:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error_type": "session_run_locked",
                        "detail": "当前 Session 正在执行 AI 请求，请稍后重试恢复审批。",
                    },
                )
            latest = self._find_approval(session, approval.approval_id)
            if latest.status != "expired" or not latest.resume_failed:
                return latest
            return self._resume_expired_cleanup(
                identity,
                session,
                latest,
                agent_runtime=agent_runtime,
                operation_service=operation_service,
                task_service=task_service,
            )

    def _resume_expired_cleanup(
        self,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        *,
        agent_runtime: Any,
        operation_service: Any,
        task_service: Any,
    ) -> ApprovalRecord:
        try:
            result = self._resume_decided(
                identity,
                session,
                approval,
                "rejected",
                agent_runtime=agent_runtime,
                operation_service=operation_service,
                task_service=task_service,
                reject_message="审批超时，操作已自动取消，未执行 DBAAS 变更。",
                persist_assistant_message=False,
                allow_next_approval=False,
            )
        except Exception:
            logger.exception(
                "expired approval resume failed approval_id=%s session_id=%s",
                approval.approval_id,
                session.session_id,
            )
            return self._find_approval(session, approval.approval_id)
        logger.info(
            "expired approval resume cleared approval_id=%s session_id=%s",
            approval.approval_id,
            session.session_id,
        )
        return result.approval

    def _expire_and_resume(
        self,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        *,
        agent_runtime: Any,
        operation_service: Any,
        task_service: Any,
    ) -> ApprovalRecord:
        marked = self._mark_expired(session, approval)
        logger.info(
            "approval expired approval_id=%s session_id=%s",
            marked.approval_id,
            session.session_id,
        )
        return self._resume_expired_cleanup(
            identity,
            session,
            marked,
            agent_runtime=agent_runtime,
            operation_service=operation_service,
            task_service=task_service,
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

    @staticmethod
    def _raise_approval_expired(approval: ApprovalRecord) -> None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "approval_expired",
                "detail": "审批已超时，操作已取消。",
                "approval": approval.model_dump(mode="json"),
            },
        )

    def _emit_task_creation_notice_if_needed(
        self,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        *,
        decision: ApiDecision,
        tasks: list[TaskRecord],
    ) -> tuple[ApprovalRecord, ChatMessage | None]:
        if decision != "approved" or not tasks or approval.task_creation_notice_emitted:
            return approval, None

        system_message = self.session_service.append_system_message(
            identity,
            session.session_id,
            _build_task_creation_notice(tasks),
        )
        marked = approval.model_copy(update={"task_creation_notice_emitted": True})
        self.repository.append_approval(session.user_id, session.session_id, marked)
        return marked, system_message

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

    def _load_current_services(
        self,
        identity: Identity,
        tool_calls: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        if self.current_value_client is None:
            return {}
        service_names = {
            str(tool_args.get("service_name") or "")
            for _tool_name, tool_args in tool_calls
            if tool_args.get("service_name")
        }
        snapshots: dict[str, dict[str, Any]] = {}
        for service_name in sorted(service_names):
            try:
                snapshot = self.current_value_client.get_service(
                    identity,
                    service_name,
                    timeout_seconds=self.current_value_timeout_seconds,
                )
            except DbaasWriteClientError:
                continue
            if isinstance(snapshot, dict):
                snapshots[service_name] = snapshot
        return snapshots

    def _assert_no_task_conflicts(
        self,
        session: SessionMeta,
        approval: ApprovalRecord,
        task_service: Any,
    ) -> None:
        for tool_call in approval.interrupted_tool_calls:
            config = require_action_config(tool_call.tool_name)
            if config.execution_mode != "async":
                continue
            target = _service_target(tool_call.tool_args)
            try:
                task_service.ensure_no_conflicting_task(
                    session,
                    build_operation_conflict_key(config.action, [target]),
                )
            except TaskConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error_type": "task_conflict",
                        "detail": "当前 Session 已存在同类未结束任务，未创建新的 DBAAS 任务。",
                        "existing_task": exc.task.model_dump(mode="json"),
                    },
                ) from exc

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


def _build_task_creation_notice(tasks: list[TaskRecord]) -> str:
    sorted_tasks = sorted(tasks, key=lambda task: task.created_at)
    if len(sorted_tasks) == 1:
        task = sorted_tasks[0]
        return (
            f"本次审批确认已创建异步任务 {task.task_id}，"
            "系统会在任务结束后继续提醒最终执行结果。"
        )
    return (
        f"本次审批确认已创建 {len(sorted_tasks)} 个异步任务，"
        "系统会在任务结束后继续提醒最终执行结果。"
    )


def _service_target(tool_args: dict[str, Any]) -> OperationTarget:
    service_name = str(tool_args.get("service_name") or "")
    child_service_type = str(tool_args.get("child_service_type") or "")
    qualifiers: dict[str, Any] = {}
    if child_service_type:
        qualifiers["child_service_type"] = child_service_type
    return OperationTarget(
        kind="service",
        id=service_name,
        name=service_name or None,
        qualifiers=qualifiers,
    )

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.runtime import AgentApprovalRequest, AgentReply  # noqa: E402
from dbass_ai_agent.api.deps import (  # noqa: E402
    get_agent_runtime,
    get_app_settings,
    get_current_identity,
    get_operation_service,
    get_session_service,
    get_task_service,
)
from dbass_ai_agent.api.routes_approvals import router as approvals_router  # noqa: E402
from dbass_ai_agent.api.routes_chat import router as chat_router  # noqa: E402
from dbass_ai_agent.api.routes_sessions import router as sessions_router  # noqa: E402
from dbass_ai_agent.api.routes_tasks import router as tasks_router  # noqa: E402
from dbass_ai_agent.config import Settings  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402
from dbass_ai_agent.infra.clock import utc_now  # noqa: E402
from dbass_ai_agent.operations.approval_service import ApprovalInterrupt, ApprovalService  # noqa: E402
from dbass_ai_agent.operations.models import OperationResult, OperationTarget, OperationTaskRef, TaskRecord  # noqa: E402
from dbass_ai_agent.operations.operation_service import OperationService  # noqa: E402
from dbass_ai_agent.operations.task_service import TaskService  # noqa: E402
from dbass_ai_agent.sessions.approval_store import ApprovalStore  # noqa: E402
from dbass_ai_agent.sessions.index_store import IndexStore  # noqa: E402
from dbass_ai_agent.sessions.message_store import MessageStore  # noqa: E402
from dbass_ai_agent.sessions.operation_store import OperationStore  # noqa: E402
from dbass_ai_agent.sessions.repository import SessionRepository  # noqa: E402
from dbass_ai_agent.sessions.run_lock import session_locks  # noqa: E402
from dbass_ai_agent.sessions.service import SessionService  # noqa: E402
from dbass_ai_agent.sessions.task_store import TaskStore  # noqa: E402
from dbass_ai_agent.sessions.thread_binding import ThreadBinding  # noqa: E402


class ApprovalRuntime:
    def generate_reply(self, *, identity, session, user_message):
        return AgentReply(
            run_id="run_phase7_approval",
            content="",
            mode="deepagent",
            approval_request=AgentApprovalRequest(
                action_requests=[
                    {
                        "name": "update_service_resource_tool",
                        "args": {
                            "service_name": "mysql-xf2",
                            "child_service_type": "mysql",
                            "memory": 15,
                        },
                    },
                ],
                review_configs=[
                    {
                        "action_name": "update_service_resource_tool",
                        "allowed_decisions": ["approve", "reject"],
                    },
                ],
                tool_call_ids=["call_phase7_001"],
            ),
            paused=True,
        )


class ResumeRuntime:
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        return AgentReply(
            run_id="run_phase7_resume",
            content=f"审批已{decision}",
            mode="deepagent",
        )


class ExpiringRuntime:
    def __init__(self) -> None:
        self.resume_calls = 0

    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        self.resume_calls += 1
        self.last_reject_message = reject_message
        return AgentReply(
            run_id="run_phase7_expired_resume",
            content="审批超时，操作已自动取消，未执行 DBAAS 变更。",
            mode="deepagent",
        )

    def generate_reply(self, *, identity, session, user_message):
        return AgentReply(
            run_id="run_phase7_after_expired",
            content="Session 已恢复可用。",
            mode="deepagent",
        )


class FailingExpiringRuntime:
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        raise RuntimeError("checkpoint unavailable")


def unexpected_agent_runtime():
    raise AssertionError("DeepAgent runtime should not be initialized")


class ExpiringNextApprovalRuntime(ExpiringRuntime):
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        self.resume_calls += 1
        self.last_reject_message = reject_message
        return AgentReply(
            run_id="run_phase7_expired_next_approval",
            content="审批超时，操作已自动取消，未执行 DBAAS 变更。",
            mode="deepagent",
            approval_request=AgentApprovalRequest(
                action_requests=[
                    {
                        "name": "update_service_resource_tool",
                        "args": {
                            "service_name": "mysql-xf2",
                            "child_service_type": "mysql",
                            "memory": 16,
                        },
                    },
                ],
                review_configs=[
                    {
                        "action_name": "update_service_resource_tool",
                        "allowed_decisions": ["approve", "reject"],
                    },
                ],
                tool_call_ids=["call_phase7_expired_next"],
            ),
            paused=True,
        )


class RejectRuntime:
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        return AgentReply(
            run_id="run_phase7_reject",
            content="操作被系统拒绝，可能原因：用户没有权限。",
            mode="deepagent",
        )


class NestedResumeRuntime:
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        target = OperationTarget(
            kind="service",
            id="mysql-xf2",
            name="mysql-xf2",
            qualifiers={"child_service_type": "mysql"},
        )
        operation = operation_service.start_operation(
            session,
            approval=approval,
            run_id="run_phase7_nested_resume",
            action="service.resource.update",
            execution_mode="sync",
        )
        operation_service.complete_operation(
            session,
            operation,
            status="succeeded",
            result=OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=operation.action,
                targets=[target],
                execution_mode="sync",
                status="succeeded",
                summary="已更新 mysql-xf2/mysql 的资源规格。",
            ),
        )
        return AgentReply(
            run_id="run_phase7_nested_resume",
            content="",
            mode="deepagent",
            approval_request=AgentApprovalRequest(
                action_requests=[
                    {
                        "name": "update_service_storage_tool",
                        "args": {
                            "service_name": "mysql-xf2",
                            "child_service_type": "mysql",
                            "data_volume_size": 600,
                        },
                    },
                ],
                review_configs=[
                    {
                        "action_name": "update_service_storage_tool",
                        "allowed_decisions": ["approve", "reject"],
                    },
                ],
                tool_call_ids=["call_phase7_nested_002"],
            ),
            paused=True,
        )


class BatchResumeRuntime:
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        for tool_call in approval.interrupted_tool_calls:
            args = tool_call.tool_args
            target = OperationTarget(
                kind="service",
                id=args["service_name"],
                name=args["service_name"],
                qualifiers={"child_service_type": args["child_service_type"]},
            )
            operation = operation_service.start_operation(
                session,
                approval=approval,
                run_id="run_phase7_batch_resume",
                action="service.resource.update",
                execution_mode="sync",
                tool_name=tool_call.tool_name,
                tool_args=args,
                targets=[target],
            )
            operation_service.complete_operation(
                session,
                operation,
                status="succeeded",
                result=OperationResult(
                    operation_id=operation.operation_id,
                    approval_id=operation.approval_id,
                    action=operation.action,
                    targets=[target],
                    execution_mode="sync",
                    status="succeeded",
                    summary=f"已更新 {args['service_name']}/{args['child_service_type']} 的资源规格。",
                ),
            )
        return AgentReply(
            run_id="run_phase7_batch_resume",
            content="批量审批已执行。",
            mode="deepagent",
        )


class AsyncTaskResumeRuntime:
    def resume_approval(
        self,
        *,
        identity,
        session,
        approval,
        decision,
        operation_service,
        task_service,
        reject_message=None,
    ):
        created_task_ids: list[str] = []
        for index, tool_call in enumerate(approval.interrupted_tool_calls, start=1):
            args = tool_call.tool_args
            child_service_type = args.get("child_service_type", "mysql")
            target = OperationTarget(
                kind="service",
                id=args.get("service_name", "mysql-xf2"),
                name=args.get("service_name", "mysql-xf2"),
                qualifiers={"child_service_type": child_service_type},
            )
            operation = operation_service.start_operation(
                session,
                approval=approval,
                run_id="run_phase7_async_resume",
                action="service.image.upgrade",
                execution_mode="async",
                tool_name=tool_call.tool_name,
                tool_args=args,
                targets=[target],
            )
            task_id = f"task-async-{index:03d}"
            task = task_service.create_task_record(
                session,
                task_id=task_id,
                operation_id=operation.operation_id,
                action="service.image.upgrade",
                targets=[target],
                dbaas_type="service.image.upgrade",
                source_status="RUNNING",
                message="image upgrade task created",
            )
            operation_service.complete_operation(
                session,
                operation,
                status="task_created",
                result=OperationResult(
                    operation_id=operation.operation_id,
                    approval_id=operation.approval_id,
                    action=operation.action,
                    targets=[target],
                    execution_mode="async",
                    status="task_created",
                    summary=f"已创建 {target.id}/{child_service_type} 镜像升级任务 {task_id}。",
                    task=OperationTaskRef(
                        task_id=task.task_id,
                        type=task.dbaas_type,
                        status=task.status,
                    ),
                    details={"task": task.model_dump(mode="json")},
                ),
            )
            created_task_ids.append(task_id)
        return AgentReply(
            run_id="run_phase7_async_resume",
            content=f"已创建异步任务：{', '.join(created_task_ids)}。需要查询执行结果吗？",
            mode="deepagent",
        )


class Phase7ApprovalApiTests(unittest.TestCase):
    def test_send_message_creates_pending_approval_and_blocks_next_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ApprovalRuntime()

            with TestClient(app) as client:
                session_id = client.post("/api/v1/sessions", json={"title": "phase7"}).json()[
                    "session"
                ]["meta"]["session_id"]
                response = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "帮我将 mysql-xf2/mysql 内存扩到 15g"},
                )
                blocked = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "继续"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["paused"])
            self.assertIsNone(payload["assistant_message"])
            self.assertEqual(payload["approval"]["status"], "pending")
            self.assertEqual(payload["approval"]["proposal"]["items"][0]["action"], "service.resource.update")
            self.assertEqual(
                _seconds_between(payload["approval"]["created_at"], payload["approval"]["expires_at"]),
                300,
            )
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["detail"]["error_type"], "session_has_pending_approval")

    def test_send_message_rechecks_pending_approval_after_run_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 locked pending check")
            app = FastAPI()
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ApprovalRuntime()

            def create_pending_under_lock(approval_service, identity_arg, session_id_arg, **kwargs):
                self.assertTrue(kwargs["run_lock_already_held"])
                self.assertTrue(session_locks.is_run_locked(session_id_arg))
                session = approval_service.session_service.get_session(identity_arg, session_id_arg).meta
                approval_service.create_approval(
                    identity_arg,
                    session,
                    run_id="run_phase7_pending_after_lock",
                    request_message_id="msg_request",
                    interrupt=ApprovalInterrupt(
                        action_requests=[
                            {
                                "name": "update_service_resource_tool",
                                "args": {
                                    "service_name": "mysql-xf2",
                                    "child_service_type": "mysql",
                                    "memory": 15,
                                },
                            },
                        ],
                        review_configs=[
                            {
                                "action_name": "update_service_resource_tool",
                                "allowed_decisions": ["approve", "reject"],
                            },
                        ],
                        tool_call_ids=["call_phase7_pending_after_lock"],
                    ),
                )
                return []

            with patch.object(
                ApprovalService,
                "expire_pending_approvals",
                autospec=True,
                side_effect=create_pending_under_lock,
            ):
                with TestClient(app) as client:
                    response = client.post(
                        f"/api/v1/sessions/{detail.meta.session_id}/messages",
                        json={"content": "继续"},
                    )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["error_type"], "session_has_pending_approval")
            current = service.get_session(identity, detail.meta.session_id)
            self.assertEqual(len(current.messages), 0)
            self.assertEqual(current.approvals[-1].status, "pending")

    def test_stream_message_rechecks_pending_approval_after_run_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 stream locked pending check")
            app = FastAPI()
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ApprovalRuntime()

            def create_pending_under_lock(approval_service, identity_arg, session_id_arg, **kwargs):
                self.assertTrue(kwargs["run_lock_already_held"])
                self.assertTrue(session_locks.is_run_locked(session_id_arg))
                session = approval_service.session_service.get_session(identity_arg, session_id_arg).meta
                approval_service.create_approval(
                    identity_arg,
                    session,
                    run_id="run_phase7_stream_pending_after_lock",
                    request_message_id="msg_request",
                    interrupt=ApprovalInterrupt(
                        action_requests=[
                            {
                                "name": "update_service_resource_tool",
                                "args": {
                                    "service_name": "mysql-xf2",
                                    "child_service_type": "mysql",
                                    "memory": 15,
                                },
                            },
                        ],
                        review_configs=[
                            {
                                "action_name": "update_service_resource_tool",
                                "allowed_decisions": ["approve", "reject"],
                            },
                        ],
                        tool_call_ids=["call_phase7_stream_pending_after_lock"],
                    ),
                )
                return []

            with patch.object(
                ApprovalService,
                "expire_pending_approvals",
                autospec=True,
                side_effect=create_pending_under_lock,
            ):
                with TestClient(app) as client:
                    response = client.post(
                        f"/api/v1/sessions/{detail.meta.session_id}/messages/stream",
                        json={"content": "继续"},
                    )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["error_type"], "session_has_pending_approval")
            current = service.get_session(identity, detail.meta.session_id)
            self.assertEqual(len(current.messages), 0)
            self.assertEqual(current.approvals[-1].status, "pending")

    def test_expired_approval_resumes_reject_and_allows_next_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 expired")
            approval_service = ApprovalService(service.repository, service)
            approval = approval_service.create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_expired",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_expired"],
                ),
            )
            expired_pending = approval.model_copy(update={"expires_at": utc_now() - timedelta(seconds=1)})
            service.repository.append_approval(detail.meta.user_id, detail.meta.session_id, expired_pending)
            runtime = ExpiringRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/messages",
                    json={"content": "继续"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(runtime.resume_calls, 1)
            self.assertEqual(runtime.last_reject_message, "审批超时，操作已自动取消，未执行 DBAAS 变更。")
            self.assertEqual(response.json()["assistant_message"]["content"], "Session 已恢复可用。")
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "expired")
            self.assertFalse(approvals[-1].resume_failed)

    def test_get_session_expires_pending_approval_and_clears_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 get session expires")
            approval = _create_expired_resource_approval(identity, detail, service)
            runtime = ExpiringRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(sessions_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(runtime.resume_calls, 1)
            approvals = response.json()["session"]["approvals"]
            self.assertEqual(approvals[-1]["approval_id"], approval.approval_id)
            self.assertEqual(approvals[-1]["status"], "expired")
            self.assertFalse(approvals[-1]["resume_failed"])
            messages = service.get_session(identity, detail.meta.session_id).messages
            self.assertEqual(messages, [])

    def test_get_session_without_expired_approval_does_not_initialize_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 get session no expiry")
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(sessions_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = unexpected_agent_runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["session"]["meta"]["session_id"], detail.meta.session_id)

    def test_get_approvals_expires_pending_approval_and_records_resume_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 get approvals expires")
            approval = _create_expired_resource_approval(identity, detail, service)
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: FailingExpiringRuntime()
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}/approvals")

            self.assertEqual(response.status_code, 200)
            approvals = response.json()["items"]
            self.assertEqual(approvals[-1]["approval_id"], approval.approval_id)
            self.assertEqual(approvals[-1]["status"], "expired")
            self.assertTrue(approvals[-1]["resume_failed"])
            self.assertIn("checkpoint unavailable", approvals[-1]["resume_error"])

    def test_get_approvals_without_expired_approval_does_not_initialize_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 get approvals no expiry")
            approval = ApprovalService(service.repository, service).create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_no_expiry",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_no_expiry"],
                ),
            )
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = unexpected_agent_runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}/approvals")

            self.assertEqual(response.status_code, 200)
            approvals = response.json()["items"]
            self.assertEqual(approvals[-1]["approval_id"], approval.approval_id)
            self.assertEqual(approvals[-1]["status"], "pending")

    def test_expiration_cleanup_requires_runtime_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 expiry requires runtime")
            approval = _create_expired_resource_approval(identity, detail, service)
            approval_service = ApprovalService(service.repository, service)

            with self.assertRaises(HTTPException) as raised:
                approval_service.expire_pending_approvals(identity, detail.meta.session_id)

            self.assertEqual(raised.exception.status_code, 500)
            self.assertEqual(
                raised.exception.detail["error_type"],
                "approval_cleanup_dependencies_missing",
            )
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].approval_id, approval.approval_id)
            self.assertEqual(approvals[-1].status, "pending")

    def test_get_session_skips_expiration_cleanup_when_run_lock_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 get session locked expiry")
            approval = _create_expired_resource_approval(identity, detail, service)
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(sessions_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = unexpected_agent_runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with session_locks.acquire_run_lock(detail.meta.session_id) as acquired:
                self.assertTrue(acquired)
                with TestClient(app) as client:
                    response = client.get(f"/api/v1/sessions/{detail.meta.session_id}")

            self.assertEqual(response.status_code, 200)
            payload_approval = response.json()["session"]["approvals"][-1]
            self.assertEqual(payload_approval["approval_id"], approval.approval_id)
            self.assertEqual(payload_approval["status"], "pending")
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "pending")

    def test_get_approvals_skips_expiration_cleanup_when_run_lock_busy_without_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 approvals locked expiry")
            approval = _create_expired_resource_approval(identity, detail, service)
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = unexpected_agent_runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with session_locks.acquire_run_lock(detail.meta.session_id) as acquired:
                self.assertTrue(acquired)
                with TestClient(app) as client:
                    response = client.get(f"/api/v1/sessions/{detail.meta.session_id}/approvals")

            self.assertEqual(response.status_code, 200)
            payload_approval = response.json()["items"][-1]
            self.assertEqual(payload_approval["approval_id"], approval.approval_id)
            self.assertEqual(payload_approval["status"], "pending")
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "pending")

    def test_approval_proposal_includes_current_values_when_snapshot_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 current values")
            approval_service = ApprovalService(
                service.repository,
                service,
                current_value_client=FakeCurrentValueClient(),
                current_value_timeout_seconds=1,
            )

            approval = approval_service.create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_current_values",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                        {
                            "name": "update_service_storage_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "data_volume_size": 600,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                        {
                            "action_name": "update_service_storage_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_current_001", "call_phase7_current_002"],
                ),
            )

            memory_param = approval.proposal.items[0].parameters[0]
            storage_param = approval.proposal.items[1].parameters[0]
            self.assertEqual(memory_param.key, "memory")
            self.assertEqual(memory_param.current_value, 8)
            self.assertEqual(memory_param.current_unit, "GB")
            self.assertEqual(storage_param.key, "data_volume_size")
            self.assertEqual(storage_param.current_value, 500)
            self.assertEqual(storage_param.current_unit, "GB")

    def test_approval_decision_resumes_and_persists_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 decision")
            approval_service = ApprovalService(service.repository, service)
            approval = approval_service.create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_approval",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_001"],
                ),
            )
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ResumeRuntime()

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["approval"]["status"], "approved")
            self.assertEqual(payload["assistant_message"]["content"], "审批已approved")
            self.assertEqual(service.get_session(identity, detail.meta.session_id).messages[-1].role, "assistant")

    def test_user_rejected_approval_persists_fixed_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 reject")
            approval_service = ApprovalService(service.repository, service)
            approval = approval_service.create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_approval",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_001"],
                ),
            )
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: RejectRuntime()

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "rejected"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            expected = "用户已拒绝该操作，未执行 DBAAS 变更。"
            self.assertEqual(payload["approval"]["status"], "rejected")
            self.assertEqual(payload["assistant_message"]["content"], expected)
            self.assertEqual(
                service.get_session(identity, detail.meta.session_id).messages[-1].content,
                expected,
            )

    def test_approval_decision_can_return_next_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 next approval")
            approval_service = ApprovalService(service.repository, service)
            approval = approval_service.create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_approval",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_001"],
                ),
            )
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: NestedResumeRuntime()

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["approval"]["status"], "approved")
            self.assertEqual(payload["operations"][0]["status"], "succeeded")
            self.assertTrue(payload["paused"])
            self.assertEqual(payload["next_approval"]["status"], "pending")
            self.assertEqual(
                payload["next_approval"]["interrupted_tool_calls"][0]["tool_name"],
                "update_service_storage_tool",
            )
            session = service.get_session(identity, detail.meta.session_id)
            self.assertEqual(
                [item.status for item in session.approvals],
                ["approved", "pending"],
            )
            self.assertEqual(len(session.operations), 1)
            self.assertEqual(session.operations[0].status, "succeeded")

    def test_batch_approval_displays_all_items_and_returns_all_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 batch approval")
            approval_service = ApprovalService(service.repository, service)
            approval = approval_service.create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_batch_approval",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "memory": 15,
                            },
                        },
                        {
                            "name": "update_service_resource_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "proxy",
                                "memory": 4,
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                        {
                            "action_name": "update_service_resource_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_batch_001", "call_phase7_batch_002"],
                ),
            )
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: BatchResumeRuntime()

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(len(approval.proposal.items), 2)
            self.assertEqual(len(approval.interrupted_tool_calls), 2)
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["approval"]["status"], "approved")
            self.assertEqual(len(payload["operations"]), 2)
            self.assertEqual(
                {item["tool_call_id"] for item in payload["operations"]},
                {"call_phase7_batch_001", "call_phase7_batch_002"},
            )
            self.assertEqual(payload["tasks"], [])

    def test_async_approval_decision_emits_task_creation_system_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 async notice")
            approval = _create_async_approval(identity, detail, service, count=1)
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: AsyncTaskResumeRuntime()

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["approval"]["status"], "approved")
            self.assertTrue(payload["approval"]["task_creation_notice_emitted"])
            self.assertEqual(payload["system_message"]["role"], "system")
            self.assertEqual(
                payload["system_message"]["content"],
                "本次审批确认已创建异步任务 task-async-001，系统会在任务结束后继续提醒最终执行结果。",
            )
            self.assertEqual(len(payload["tasks"]), 1)
            self.assertEqual(payload["tasks"][0]["status"], "running")
            messages = service.get_session(identity, detail.meta.session_id).messages
            self.assertEqual([message.role for message in messages], ["assistant", "system"])
            self.assertEqual(messages[-1].content, payload["system_message"]["content"])

    def test_batch_async_approval_emits_one_creation_notice_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 batch async notice")
            approval = _create_async_approval(identity, detail, service, count=2)
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: AsyncTaskResumeRuntime()

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )
                repeated = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(len(payload["tasks"]), 2)
            self.assertEqual(payload["system_message"]["role"], "system")
            self.assertEqual(
                payload["system_message"]["content"],
                "本次审批确认已创建 2 个异步任务，系统会在任务结束后继续提醒最终执行结果。",
            )
            self.assertEqual(repeated.status_code, 200)
            self.assertIsNone(repeated.json()["system_message"])
            messages = service.get_session(identity, detail.meta.session_id).messages
            self.assertEqual(
                [message.content for message in messages if message.role == "system"],
                ["本次审批确认已创建 2 个异步任务，系统会在任务结束后继续提醒最终执行结果。"],
            )

    def test_async_approval_decision_returns_409_for_existing_conflicting_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 task conflict")
            approval = _create_async_approval(identity, detail, service, count=1)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            service.repository.append_task(
                detail.meta.user_id,
                detail.meta.session_id,
                _running_task(detail.meta.session_id),
            )
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: AsyncTaskResumeRuntime()
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 409)
            payload = response.json()
            self.assertEqual(payload["detail"]["error_type"], "task_conflict")
            self.assertEqual(payload["detail"]["existing_task"]["task_id"], "task-001")
            self.assertEqual(service.get_session(identity, detail.meta.session_id).approvals[-1].status, "pending")

    def test_batch_async_approval_rejects_duplicate_conflict_key_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 duplicate async batch")
            approval = ApprovalService(service.repository, service).create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_duplicate_async_batch",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "create_service_image_upgrade_task_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "image_type": "mysql",
                                "image_tag": "8.0.38",
                            },
                        },
                        {
                            "name": "create_service_image_upgrade_task_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "image_type": "mysql",
                                "image_tag": "8.0.39",
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "create_service_image_upgrade_task_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                        {
                            "action_name": "create_service_image_upgrade_task_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_duplicate_001", "call_phase7_duplicate_002"],
                ),
            )
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: AsyncTaskResumeRuntime()
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 409)
            payload = response.json()
            self.assertEqual(payload["detail"]["error_type"], "task_conflict")
            self.assertIn("operation_conflict_key", payload["detail"])
            current = service.get_session(identity, detail.meta.session_id)
            self.assertEqual(current.approvals[-1].status, "pending")
            self.assertEqual(task_service.list_tasks(detail.meta), [])

    def test_approval_decision_rechecks_expiration_after_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 decision lock expiry")
            approval = _create_expired_resource_approval(identity, detail, service).model_copy(
                update={"expires_at": utc_now() + timedelta(minutes=1)}
            )
            service.repository.append_approval(detail.meta.user_id, detail.meta.session_id, approval)
            runtime = ExpiringRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with patch.object(ApprovalService, "_is_expired", return_value=True):
                with TestClient(app) as client:
                    response = client.post(
                        f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                        json={"decision": "approved"},
                    )

            self.assertEqual(response.status_code, 409)
            payload = response.json()
            self.assertEqual(payload["detail"]["error_type"], "approval_expired")
            self.assertEqual(runtime.resume_calls, 1)
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "expired")
            self.assertFalse(approvals[-1].resume_failed)

    def test_expired_decision_retries_failed_resume_and_returns_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 expired decision retry")
            approval = _create_expired_resource_approval(identity, detail, service)
            failed = approval.model_copy(
                update={
                    "status": "expired",
                    "expired_at": utc_now(),
                    "resume_failed": True,
                    "resume_error": "previous failure",
                    "resume_last_attempt_at": utc_now(),
                }
            )
            service.repository.append_approval(detail.meta.user_id, detail.meta.session_id, failed)
            runtime = ExpiringRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 409)
            payload = response.json()
            self.assertEqual(payload["detail"]["error_type"], "approval_expired")
            self.assertEqual(runtime.resume_calls, 1)
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "expired")
            self.assertFalse(approvals[-1].resume_failed)
            self.assertEqual(service.get_session(identity, detail.meta.session_id).messages, [])

    def test_expired_pending_decision_requires_run_lock_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 pending expiry locked")
            approval = _create_expired_resource_approval(identity, detail, service)
            runtime = ExpiringRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with session_locks.acquire_run_lock(detail.meta.session_id) as acquired:
                self.assertTrue(acquired)
                with TestClient(app) as client:
                    response = client.post(
                        f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                        json={"decision": "approved"},
                    )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["error_type"], "session_run_locked")
            self.assertEqual(runtime.resume_calls, 0)
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "pending")

    def test_expired_decision_resume_retry_requires_run_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 expired decision locked")
            approval = _create_expired_resource_approval(identity, detail, service)
            failed = approval.model_copy(
                update={
                    "status": "expired",
                    "expired_at": utc_now(),
                    "resume_failed": True,
                    "resume_error": "previous failure",
                    "resume_last_attempt_at": utc_now(),
                }
            )
            service.repository.append_approval(detail.meta.user_id, detail.meta.session_id, failed)
            runtime = ExpiringRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(approvals_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with session_locks.acquire_run_lock(detail.meta.session_id) as acquired:
                self.assertTrue(acquired)
                with TestClient(app) as client:
                    response = client.post(
                        f"/api/v1/sessions/{detail.meta.session_id}/approvals/{approval.approval_id}/decision",
                        json={"decision": "approved"},
                    )

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["error_type"], "session_run_locked")
            self.assertEqual(runtime.resume_calls, 0)
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "expired")
            self.assertTrue(approvals[-1].resume_failed)

    def test_archive_blocks_when_expired_approval_resume_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 archive expired resume failed")
            _create_expired_resource_approval(identity, detail, service)
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(sessions_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: FailingExpiringRuntime()
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.post(f"/api/v1/sessions/{detail.meta.session_id}/archive")

            self.assertEqual(response.status_code, 409)
            payload = response.json()
            self.assertEqual(payload["detail"]["error_type"], "expired_approval_resume_failed")
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(approvals[-1].status, "expired")
            self.assertTrue(approvals[-1].resume_failed)

    def test_archive_returns_409_when_session_run_lock_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 archive locked")
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(sessions_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ExpiringRuntime()
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with session_locks.acquire_run_lock(detail.meta.session_id) as acquired:
                self.assertTrue(acquired)
                with TestClient(app) as client:
                    response = client.post(f"/api/v1/sessions/{detail.meta.session_id}/archive")

            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["error_type"], "session_run_locked")

    def test_query_expiration_does_not_restore_archived_session_or_create_next_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="phase7 archived expiry cleanup")
            _create_expired_resource_approval(identity, detail, service)
            service.archive_session(identity, detail.meta.session_id)
            runtime = ExpiringNextApprovalRuntime()
            operation_service = OperationService(service.repository)
            task_service = TaskService(service.repository, _dbaas_config(tmpdir))
            app = FastAPI()
            app.include_router(sessions_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_operation_service] = lambda: operation_service
            app.dependency_overrides[get_task_service] = lambda: task_service

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["session"]["meta"]["status"], "archived")
            self.assertEqual(runtime.resume_calls, 1)
            approvals = service.get_session(identity, detail.meta.session_id).approvals
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0].status, "expired")
            self.assertFalse(approvals[0].resume_failed)
            self.assertEqual(service.get_session(identity, detail.meta.session_id).messages, [])


class TaskLazyRefreshTests(unittest.TestCase):
    def test_refresh_task_skips_terminal_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="terminal task")
            task_service = TaskService(
                service.repository,
                _dbaas_config(tmpdir),
                write_client=UnexpectedTaskClient(),
            )
            terminal = _running_task(detail.meta.session_id).model_copy(
                update={"status": "succeeded", "source_status": "SUCCESS"}
            )
            service.repository.append_task(detail.meta.user_id, detail.meta.session_id, terminal)

            refreshed = task_service.refresh_task(identity, detail.meta, terminal)

            self.assertEqual(refreshed.status, "succeeded")
            self.assertEqual(refreshed.source_status, "SUCCESS")
            self.assertEqual(task_service.list_tasks(detail.meta), [terminal])

    def test_tasks_endpoint_lazy_refreshes_current_session_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="task route")
            task_service = TaskService(
                service.repository,
                _dbaas_config(tmpdir),
                write_client=FakeTaskClient(),
            )
            service.repository.append_task(
                detail.meta.user_id,
                detail.meta.session_id,
                _running_task(detail.meta.session_id),
            )
            app = FastAPI()
            app.include_router(tasks_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_task_service] = lambda: task_service
            app.dependency_overrides[get_app_settings] = lambda: Settings(
                dbaas_task_refresh_interval_seconds=1,
            )

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}/tasks")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["items"][0]["status"], "succeeded")
            self.assertEqual(payload["items"][0]["source_status"], "SUCCESS")

    def test_task_events_streams_task_status_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="task events")
            task_service = TaskService(
                service.repository,
                _dbaas_config(tmpdir),
                write_client=FakeTaskClient(),
            )
            service.repository.append_task(
                detail.meta.user_id,
                detail.meta.session_id,
                _running_task(detail.meta.session_id),
            )
            app = FastAPI()
            app.include_router(tasks_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_task_service] = lambda: task_service
            app.dependency_overrides[get_app_settings] = lambda: Settings(
                dbaas_task_refresh_interval_seconds=1,
            )

            with TestClient(app) as client:
                with client.stream(
                    "GET",
                    f"/api/v1/sessions/{detail.meta.session_id}/tasks/events",
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

            events = _parse_sse_events(body)
            self.assertEqual(
                [event_name for event_name, _payload in events],
                ["task_status_changed", "task_terminal_notice_emitted"],
            )
            payload = events[0][1]
            self.assertEqual(payload["session_id"], detail.meta.session_id)
            self.assertEqual(payload["task"]["task_id"], "task-001")
            self.assertEqual(payload["task"]["previous_status"], "running")
            self.assertEqual(payload["task"]["status"], "succeeded")
            notice = events[1][1]
            self.assertEqual(notice["session_id"], detail.meta.session_id)
            self.assertEqual(notice["tasks"][0]["task_id"], "task-001")
            self.assertTrue(notice["tasks"][0]["terminal_notice_emitted"])
            self.assertEqual(notice["system_message"]["role"], "system")
            messages = service.get_session(identity, detail.meta.session_id).messages
            self.assertEqual([message.role for message in messages], ["system"])
            self.assertEqual(messages[0].content, "当前异步操作关联的异步任务 task-001 已成功。")
            self.assertTrue(task_service.list_tasks(detail.meta)[0].terminal_notice_emitted)

            with TestClient(app) as client:
                with client.stream(
                    "GET",
                    f"/api/v1/sessions/{detail.meta.session_id}/tasks/events",
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    second_body = "".join(response.iter_text())

            self.assertEqual(_parse_sse_events(second_body), [])

    def test_task_terminal_notice_does_not_restore_archived_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="archived task notice")
            task_service = TaskService(
                service.repository,
                _dbaas_config(tmpdir),
                write_client=FakeTaskClient(),
            )
            service.repository.append_task(
                detail.meta.user_id,
                detail.meta.session_id,
                _running_task(detail.meta.session_id),
            )
            service.archive_session(identity, detail.meta.session_id)
            app = FastAPI()
            app.include_router(tasks_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_task_service] = lambda: task_service
            app.dependency_overrides[get_app_settings] = lambda: Settings(
                dbaas_task_refresh_interval_seconds=1,
            )

            with TestClient(app) as client:
                with client.stream(
                    "GET",
                    f"/api/v1/sessions/{detail.meta.session_id}/tasks/events",
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

            events = _parse_sse_events(body)
            self.assertEqual(events[-1][0], "task_terminal_notice_emitted")
            self.assertEqual(events[-1][1]["system_message"]["role"], "system")
            current = service.get_session(identity, detail.meta.session_id)
            self.assertEqual(current.meta.status, "archived")
            self.assertEqual([message.role for message in current.messages], ["system"])

    def test_task_events_groups_batch_terminal_notice_by_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="task batch notice")
            approval = ApprovalService(service.repository, service).create_approval(
                identity,
                detail.meta,
                run_id="run_phase7_batch_task_approval",
                request_message_id="msg_request",
                interrupt=ApprovalInterrupt(
                    action_requests=[
                        {
                            "name": "create_service_image_upgrade_task_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "mysql",
                                "image_type": "mysql",
                                "image_tag": "8.0.38",
                            },
                        },
                        {
                            "name": "create_service_image_upgrade_task_tool",
                            "args": {
                                "service_name": "mysql-xf2",
                                "child_service_type": "proxy",
                                "image_type": "proxy",
                                "image_tag": "8.0.38",
                            },
                        },
                    ],
                    review_configs=[
                        {
                            "action_name": "create_service_image_upgrade_task_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                        {
                            "action_name": "create_service_image_upgrade_task_tool",
                            "allowed_decisions": ["approve", "reject"],
                        },
                    ],
                    tool_call_ids=["call_phase7_task_001", "call_phase7_task_002"],
                ),
            )
            operation_service = OperationService(service.repository)
            first_target = OperationTarget(
                kind="service",
                id="mysql-xf2",
                name="mysql-xf2",
                qualifiers={"child_service_type": "mysql"},
            )
            second_target = OperationTarget(
                kind="service",
                id="mysql-xf2",
                name="mysql-xf2",
                qualifiers={"child_service_type": "proxy"},
            )
            first_operation = operation_service.start_operation(
                detail.meta,
                approval=approval,
                run_id="run_phase7_batch_task_resume",
                action="service.image.upgrade",
                execution_mode="async",
                tool_name="create_service_image_upgrade_task_tool",
                tool_args=approval.interrupted_tool_calls[0].tool_args,
                targets=[first_target],
            )
            second_operation = operation_service.start_operation(
                detail.meta,
                approval=approval,
                run_id="run_phase7_batch_task_resume",
                action="service.image.upgrade",
                execution_mode="async",
                tool_name="create_service_image_upgrade_task_tool",
                tool_args=approval.interrupted_tool_calls[1].tool_args,
                targets=[second_target],
            )
            task_service = TaskService(
                service.repository,
                _dbaas_config(tmpdir),
                write_client=StaggeredTaskClient(),
            )
            first = _running_task(detail.meta.session_id).model_copy(
                update={"operation_id": first_operation.operation_id}
            )
            second = _running_task(detail.meta.session_id).model_copy(
                update={
                    "task_id": "task-002",
                    "operation_id": second_operation.operation_id,
                    "operation_conflict_key": "service.image.upgrade|service:mysql-xf2:child_service_type=proxy",
                    "targets": [second_target],
                }
            )
            service.repository.append_task(detail.meta.user_id, detail.meta.session_id, first)
            service.repository.append_task(detail.meta.user_id, detail.meta.session_id, second)
            app = FastAPI()
            app.include_router(tasks_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_task_service] = lambda: task_service
            app.dependency_overrides[get_app_settings] = lambda: Settings(
                dbaas_task_refresh_interval_seconds=1,
            )

            with TestClient(app) as client:
                with client.stream(
                    "GET",
                    f"/api/v1/sessions/{detail.meta.session_id}/tasks/events",
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

            events = _parse_sse_events(body)
            self.assertEqual(
                [event_name for event_name, _payload in events],
                [
                    "task_status_changed",
                    "task_status_changed",
                    "task_terminal_notice_emitted",
                ],
            )
            self.assertEqual(events[-1][1]["group_key"], f"approval:{approval.approval_id}")
            self.assertEqual(
                {task["task_id"] for task in events[-1][1]["tasks"]},
                {"task-001", "task-002"},
            )
            messages = service.get_session(identity, detail.meta.session_id).messages
            self.assertEqual(messages[0].role, "system")
            self.assertEqual(messages[0].content, "本次审批确认关联的异步任务已全部结束：2 个成功。")
            self.assertEqual(
                [task.terminal_notice_emitted for task in task_service.list_tasks(detail.meta)],
                [True, True],
            )

    def test_lazy_refresh_updates_non_terminal_task_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="task refresh")
            config = _dbaas_config(tmpdir)
            task_service = TaskService(
                service.repository,
                config,
                write_client=FakeTaskClient(),
            )
            task = _running_task(detail.meta.session_id)
            service.repository.append_task(detail.meta.user_id, detail.meta.session_id, task)

            refreshed = task_service.list_tasks_with_lazy_refresh(identity, detail.meta)

            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0].status, "succeeded")
            self.assertEqual(refreshed[0].source_status, "SUCCESS")
            persisted = task_service.list_tasks(detail.meta)
            self.assertEqual(persisted[0].status, "succeeded")

    def test_terminal_task_refresh_syncs_operation_final_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user=None)
            service = _session_service(tmpdir)
            detail = service.create_session(identity, title="task operation sync")
            operation_service = OperationService(service.repository)
            target = OperationTarget(
                kind="service",
                id="mysql-xf2",
                name="mysql-xf2",
                qualifiers={"child_service_type": "mysql"},
            )
            operation = operation_service.start_operation(
                detail.meta,
                approval=None,
                run_id="run_task_operation_sync",
                action="service.image.upgrade",
                execution_mode="async",
            )
            operation_service.complete_operation(
                detail.meta,
                operation,
                status="task_created",
                result=OperationResult(
                    operation_id=operation.operation_id,
                    approval_id=None,
                    action=operation.action,
                    targets=[target],
                    execution_mode="async",
                    status="task_created",
                    summary="已创建镜像升级任务 task-001。",
                    task=OperationTaskRef(
                        task_id="task-001",
                        type="service.image.upgrade",
                        status="running",
                    ),
                ),
            )
            task_service = TaskService(
                service.repository,
                _dbaas_config(tmpdir),
                write_client=FakeTaskClient(),
            )
            service.repository.append_task(
                detail.meta.user_id,
                detail.meta.session_id,
                _running_task(detail.meta.session_id).model_copy(
                    update={"operation_id": operation.operation_id}
                ),
            )

            task_service.list_tasks_with_lazy_refresh(identity, detail.meta)

            operations = operation_service.list_operations(detail.meta)
            self.assertEqual(operations[0].status, "succeeded")
            self.assertEqual(operations[0].result.status, "succeeded")
            self.assertEqual(operations[0].result.task.status, "succeeded")


class FakeTaskClient:
    def get_task(self, identity, task_id, *, timeout_seconds=None):
        return {
            "taskId": task_id,
            "type": "service.image.upgrade",
            "status": "SUCCESS",
            "message": "done",
            "reason": None,
            "resourceType": "service",
            "resourceName": "mysql-xf2",
            "result": {"ok": True},
            "createdAt": "2026-05-08T00:00:00Z",
            "updatedAt": "2026-05-08T00:00:02Z",
        }


class FakeCurrentValueClient:
    def get_service(self, identity, service_name, *, timeout_seconds=None):
        return {
            "name": service_name,
            "services": [
                {
                    "type": "mysql",
                    "platformAuto": False,
                    "units": [
                        {
                            "id": "mysql-0",
                            "cpu": 2,
                            "memory": 8,
                            "storage": {
                                "data": {"size": 500},
                                "log": {"size": 100},
                            },
                        }
                    ],
                }
            ],
        }


class StaggeredTaskClient:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def get_task(self, identity, task_id, *, timeout_seconds=None):
        self.calls[task_id] = self.calls.get(task_id, 0) + 1
        if task_id == "task-002" and self.calls[task_id] == 1:
            return {
                "taskId": task_id,
                "type": "service.image.upgrade",
                "status": "RUNNING",
                "message": "running",
                "reason": None,
                "resourceType": "service",
                "resourceName": "mysql-xf2",
                "result": None,
                "createdAt": "2026-05-08T00:00:00Z",
                "updatedAt": "2026-05-08T00:00:00Z",
            }
        return FakeTaskClient().get_task(identity, task_id, timeout_seconds=timeout_seconds)


class UnexpectedTaskClient:
    def get_task(self, identity, task_id, *, timeout_seconds=None):
        raise AssertionError("terminal tasks must not be refreshed")


def _running_task(session_id: str) -> TaskRecord:
    target = OperationTarget(
        kind="service",
        id="mysql-xf2",
        name="mysql-xf2",
        qualifiers={"child_service_type": "mysql"},
    )
    now = utc_now()
    return TaskRecord(
        task_id="task-001",
        operation_id="op-001",
        session_id=session_id,
        action="service.image.upgrade",
        operation_conflict_key="service.image.upgrade|service:mysql-xf2:child_service_type=mysql",
        targets=[target],
        dbaas_type="service.image.upgrade",
        status="running",
        source_status="RUNNING",
        message="running",
        reason=None,
        result=None,
        last_error=None,
        created_at=now,
        updated_at=now,
        last_checked_at=now,
    )


def _create_async_approval(identity: Identity, detail, service: SessionService, *, count: int):
    child_service_types = ["mysql", "proxy", "keeper", "clickhouse"]
    action_requests = []
    review_configs = []
    tool_call_ids = []
    for index in range(count):
        child_service_type = child_service_types[index]
        action_requests.append(
            {
                "name": "create_service_image_upgrade_task_tool",
                "args": {
                    "service_name": "mysql-xf2",
                    "child_service_type": child_service_type,
                    "image_type": child_service_type,
                    "image_tag": "8.0.38",
                },
            }
        )
        review_configs.append(
            {
                "action_name": "create_service_image_upgrade_task_tool",
                "allowed_decisions": ["approve", "reject"],
            }
        )
        tool_call_ids.append(f"call_phase7_async_{index + 1:03d}")
    return ApprovalService(service.repository, service).create_approval(
        identity,
        detail.meta,
        run_id="run_phase7_async_approval",
        request_message_id="msg_request",
        interrupt=ApprovalInterrupt(
            action_requests=action_requests,
            review_configs=review_configs,
            tool_call_ids=tool_call_ids,
        ),
    )


def _create_expired_resource_approval(identity: Identity, detail, service: SessionService):
    approval = ApprovalService(service.repository, service).create_approval(
        identity,
        detail.meta,
        run_id="run_phase7_expired",
        request_message_id="msg_request",
        interrupt=ApprovalInterrupt(
            action_requests=[
                {
                    "name": "update_service_resource_tool",
                    "args": {
                        "service_name": "mysql-xf2",
                        "child_service_type": "mysql",
                        "memory": 15,
                    },
                },
            ],
            review_configs=[
                {
                    "action_name": "update_service_resource_tool",
                    "allowed_decisions": ["approve", "reject"],
                },
            ],
            tool_call_ids=["call_phase7_expired"],
        ),
    )
    expired_pending = approval.model_copy(update={"expires_at": utc_now() - timedelta(seconds=1)})
    service.repository.append_approval(detail.meta.user_id, detail.meta.session_id, expired_pending)
    return expired_pending


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def _seconds_between(start: str, end: str) -> int:
    return int(
        (
            _datetime_from_payload(end) - _datetime_from_payload(start)
        ).total_seconds()
    )


def _datetime_from_payload(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _session_service(tmpdir: str) -> SessionService:
    return SessionService(
        repository=SessionRepository(
            data_root=Path(tmpdir),
            index_store=IndexStore(),
            message_store=MessageStore(),
            approval_store=ApprovalStore(),
            operation_store=OperationStore(),
            task_store=TaskStore(),
        ),
        thread_binding=ThreadBinding(),
    )


def _dbaas_config(tmpdir: str) -> DbaasConfig:
    return DbaasConfig(
        server_base_url="http://127.0.0.1:9000",
        request_timeout_seconds=1,
        workspace_dir=Path(tmpdir) / "workspace",
        sync_interval_seconds=5,
        ttl_seconds=30,
        user_active_idle_timeout_seconds=300,
        user_snapshot_refresh_wait_seconds=3,
        jq_timeout_seconds=2,
        jq_max_preview_items=50,
        jq_max_output_bytes=1024 * 1024,
        metric_snapshot_ttl_seconds=30,
        metric_snapshot_cleanup_interval_seconds=600,
        metric_refresh_lock_timeout_seconds=10,
    )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
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
from dbass_ai_agent.sessions.repository import SessionRepository  # noqa: E402
from dbass_ai_agent.sessions.service import SessionService  # noqa: E402
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
            self.assertEqual(blocked.status_code, 409)
            self.assertEqual(blocked.json()["detail"]["error_type"], "session_has_pending_approval")

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


def _session_service(tmpdir: str) -> SessionService:
    return SessionService(
        repository=SessionRepository(
            data_root=Path(tmpdir),
            index_store=IndexStore(),
            message_store=MessageStore(),
            approval_store=ApprovalStore(),
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
        jq_timeout_seconds=2,
        jq_max_preview_items=50,
        jq_max_output_bytes=1024 * 1024,
        metric_snapshot_ttl_seconds=30,
        metric_snapshot_cleanup_interval_seconds=600,
        metric_refresh_lock_timeout_seconds=10,
    )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

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
    get_current_identity,
    get_session_service,
)
from dbass_ai_agent.api.routes_approvals import router as approvals_router  # noqa: E402
from dbass_ai_agent.api.routes_chat import router as chat_router  # noqa: E402
from dbass_ai_agent.api.routes_sessions import router as sessions_router  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402
from dbass_ai_agent.infra.clock import utc_now  # noqa: E402
from dbass_ai_agent.operations.approval_service import ApprovalInterrupt, ApprovalService  # noqa: E402
from dbass_ai_agent.operations.models import OperationTarget, TaskRecord  # noqa: E402
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
                action_request={
                    "name": "update_service_resource_tool",
                    "args": {
                        "service_name": "mysql-xf2",
                        "child_service_type": "mysql",
                        "memory": 15,
                    },
                },
                review_config={
                    "action_name": "update_service_resource_tool",
                    "allowed_decisions": ["approve", "reject"],
                },
                tool_call_id="call_phase7_001",
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
            self.assertEqual(payload["approval"]["proposal"]["action"], "service.resource.update")
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
                    action_request={
                        "name": "update_service_resource_tool",
                        "args": {
                            "service_name": "mysql-xf2",
                            "child_service_type": "mysql",
                            "memory": 15,
                        },
                    },
                    review_config={
                        "action_name": "update_service_resource_tool",
                        "allowed_decisions": ["approve", "reject"],
                    },
                    tool_call_id="call_phase7_001",
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


class TaskLazyRefreshTests(unittest.TestCase):
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
            target = OperationTarget(
                kind="service",
                id="mysql-xf2",
                name="mysql-xf2",
                qualifiers={"child_service_type": "mysql"},
            )
            now = utc_now()
            task = TaskRecord(
                task_id="task-001",
                operation_id="op-001",
                session_id=detail.meta.session_id,
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
            service.repository.append_task(detail.meta.user_id, detail.meta.session_id, task)

            refreshed = task_service.list_tasks_with_lazy_refresh(identity, detail.meta)

            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0].status, "succeeded")
            self.assertEqual(refreshed[0].source_status, "SUCCESS")
            persisted = task_service.list_tasks(detail.meta)
            self.assertEqual(persisted[0].status, "succeeded")


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

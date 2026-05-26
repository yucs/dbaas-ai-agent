from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.api.deps import (  # noqa: E402
    get_agent_runtime,
    get_app_settings,
    get_approval_service,
    get_current_identity,
    get_operation_service,
    get_session_service,
    get_task_service,
)
from dbass_ai_agent.api.routes_approvals import router as approvals_router  # noqa: E402
from dbass_ai_agent.api.routes_sessions import router as sessions_router  # noqa: E402
from dbass_ai_agent.api.routes_tasks import router as tasks_router  # noqa: E402
from dbass_ai_agent.config import Settings  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402
from dbass_ai_agent.sessions.approval_store import ApprovalStore  # noqa: E402
from dbass_ai_agent.sessions.index_store import IndexStore  # noqa: E402
from dbass_ai_agent.sessions.message_store import MessageStore  # noqa: E402
from dbass_ai_agent.sessions.models import ApprovalRecord  # noqa: E402
from dbass_ai_agent.sessions.operation_store import OperationStore  # noqa: E402
from dbass_ai_agent.sessions.repository import SessionRepository  # noqa: E402
from dbass_ai_agent.sessions.service import SessionService  # noqa: E402
from dbass_ai_agent.sessions.task_store import TaskStore  # noqa: E402
from dbass_ai_agent.sessions.thread_binding import ThreadBinding  # noqa: E402


class StubBackgroundSync:
    def __init__(self) -> None:
        self.calls: list[Identity] = []

    def renew_user_lease(self, identity: Identity) -> None:
        if identity.role == "admin" or not identity.user:
            return
        self.calls.append(identity)


class StubApprovalService:
    def expire_pending_approvals_for_query(self, identity, session_id, **kwargs):
        return []

    def get_approvals(self, identity, session_id):
        return [_approval_record(session_id, "approval-list-001")]

    def decide(self, identity, session_id, approval_id, decision, **kwargs):
        return SimpleNamespace(
            approval=_approval_record(session_id, approval_id, status=decision),
            assistant_message=None,
            system_message=None,
            operations=[],
            tasks=[],
            next_approval=None,
            paused=False,
            reply=SimpleNamespace(run_id="run-approval-001", mode="deepagent"),
        )


class StubTaskService:
    def list_tasks_with_lazy_refresh(self, identity, session):
        return []

    def list_tasks(self, session):
        return []


class DbaasPrewarmApiTests(unittest.TestCase):
    def test_create_session_renews_user_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="alice", role="user", user="payment-team-prod")
            service = _build_session_service(tmpdir)
            background = StubBackgroundSync()
            app = _build_app(
                identity=identity,
                session_service=service,
                background=background,
            )

            with TestClient(app) as client:
                response = client.post("/api/v1/sessions", json={"title": "创建会话触发预热"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(background.calls, [identity])

    def test_get_approvals_renews_user_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="alice", role="user", user="payment-team-prod")
            service = _build_session_service(tmpdir)
            detail = service.create_session(identity, title="审批列表触发预热")
            background = StubBackgroundSync()
            app = _build_app(
                identity=identity,
                session_service=service,
                background=background,
                approval_service=StubApprovalService(),
            )

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}/approvals")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(background.calls, [identity])

    def test_decide_approval_renews_user_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="alice", role="user", user="payment-team-prod")
            service = _build_session_service(tmpdir)
            detail = service.create_session(identity, title="审批决策触发预热")
            background = StubBackgroundSync()
            app = _build_app(
                identity=identity,
                session_service=service,
                background=background,
                approval_service=StubApprovalService(),
            )

            with TestClient(app) as client:
                response = client.post(
                    f"/api/v1/sessions/{detail.meta.session_id}/approvals/approval-decision-001/decision",
                    json={"decision": "approved"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(background.calls, [identity])

    def test_get_session_tasks_renews_user_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="alice", role="user", user="payment-team-prod")
            service = _build_session_service(tmpdir)
            detail = service.create_session(identity, title="任务列表触发预热")
            background = StubBackgroundSync()
            app = _build_app(
                identity=identity,
                session_service=service,
                background=background,
            )

            with TestClient(app) as client:
                response = client.get(f"/api/v1/sessions/{detail.meta.session_id}/tasks")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(background.calls, [identity])

    def test_stream_session_task_events_renews_user_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="alice", role="user", user="payment-team-prod")
            service = _build_session_service(tmpdir)
            detail = service.create_session(identity, title="任务事件触发预热")
            background = StubBackgroundSync()
            app = _build_app(
                identity=identity,
                session_service=service,
                background=background,
            )

            with TestClient(app) as client:
                with client.stream(
                    "GET",
                    f"/api/v1/sessions/{detail.meta.session_id}/tasks/events",
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual("".join(response.iter_text()), "")

            self.assertEqual(background.calls, [identity])


def _build_app(
    *,
    identity: Identity,
    session_service: SessionService,
    background: StubBackgroundSync,
    approval_service: StubApprovalService | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.dbaas_background_sync = background
    app.include_router(sessions_router)
    app.include_router(approvals_router)
    app.include_router(tasks_router)
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_app_settings] = lambda: Settings(dbaas_task_refresh_interval_seconds=1)
    app.dependency_overrides[get_agent_runtime] = lambda: object()
    app.dependency_overrides[get_operation_service] = lambda: object()
    app.dependency_overrides[get_task_service] = lambda: StubTaskService()
    if approval_service is not None:
        app.dependency_overrides[get_approval_service] = lambda: approval_service
    return app


def _build_session_service(tmpdir: str) -> SessionService:
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


def _approval_record(
    session_id: str,
    approval_id: str,
    *,
    status: str = "pending",
) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        status=status,
        session_id=session_id,
        allowed_decisions=["approve", "reject"],
        created_at=datetime.now(tz=UTC),
    )


if __name__ == "__main__":
    unittest.main()

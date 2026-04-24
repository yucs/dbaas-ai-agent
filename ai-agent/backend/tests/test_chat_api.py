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

from dbass_ai_agent.agent.runtime import AgentReply
from dbass_ai_agent.api.deps import get_agent_runtime, get_current_identity, get_session_service
from dbass_ai_agent.api.routes_chat import router as chat_router
from dbass_ai_agent.api.routes_sessions import router as sessions_router
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.sessions.approval_store import ApprovalStore
from dbass_ai_agent.sessions.index_store import IndexStore
from dbass_ai_agent.sessions.message_store import MessageStore
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.service import SessionService
from dbass_ai_agent.sessions.thread_binding import ThreadBinding


class StubAgentRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate_reply(self, *, session, user_message):
        self.calls.append((session.thread_id, user_message.content))
        return AgentReply(
            run_id="run_test_001",
            content="这是回归测试回复",
            mode="deepagent",
        )


class SendMessageApiTests(unittest.TestCase):
    def test_send_message_persists_user_and_assistant_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = SessionService(
                repository=SessionRepository(
                    data_root=Path(tmpdir),
                    index_store=IndexStore(),
                    message_store=MessageStore(),
                    approval_store=ApprovalStore(),
                ),
                thread_binding=ThreadBinding(),
            )
            runtime = StubAgentRuntime()
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "回归测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]

                message_response = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "请帮我确认消息流是否正常"},
                )

            self.assertEqual(message_response.status_code, 200)
            payload = message_response.json()
            self.assertEqual(payload["mode"], "deepagent")
            self.assertEqual(payload["run_id"], "run_test_001")
            self.assertEqual(payload["user_message"]["content"], "请帮我确认消息流是否正常")
            self.assertEqual(payload["assistant_message"]["content"], "这是回归测试回复")

            detail = service.get_session(identity, session_id)
            self.assertEqual(
                [message.role for message in detail.messages],
                ["user", "assistant"],
            )
            self.assertEqual(
                [message.content for message in detail.messages],
                ["请帮我确认消息流是否正常", "这是回归测试回复"],
            )
            self.assertEqual(runtime.calls, [(detail.meta.thread_id, "请帮我确认消息流是否正常")])


if __name__ == "__main__":
    unittest.main()

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

from dbass_ai_agent.agent.runtime import AgentInvocationError, AgentReply, AgentStreamEvent
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

    def stream_reply(self, *, session, user_message):
        self.calls.append((session.thread_id, user_message.content))
        yield AgentStreamEvent(event="started", run_id="run_stream_001", mode="deepagent")
        yield AgentStreamEvent(
            event="compression_started",
            run_id="run_stream_001",
            mode="deepagent",
            content="上下文较长，正在整理早期内容。",
            details={
                "phase": "started",
                "thread_id": session.thread_id,
                "summarized_messages": 3,
                "keep": "('messages', 2)",
                "trigger": "('tokens', 10)",
                "history_path": "/conversation_history/thread.md",
                "summary_chars": None,
            },
        )
        yield AgentStreamEvent(
            event="compression_completed",
            run_id="run_stream_001",
            mode="deepagent",
            content="上下文已自动压缩。",
            details={
                "phase": "completed",
                "thread_id": session.thread_id,
                "summarized_messages": 3,
                "keep": "('messages', 2)",
                "trigger": "('tokens', 10)",
                "history_path": "/conversation_history/thread.md",
                "summary_chars": 42,
            },
        )
        yield AgentStreamEvent(
            event="token",
            run_id="run_stream_001",
            mode="deepagent",
            content="这是",
        )
        yield AgentStreamEvent(
            event="token",
            run_id="run_stream_001",
            mode="deepagent",
            content="流式回复",
        )
        yield AgentStreamEvent(
            event="completed",
            run_id="run_stream_001",
            mode="deepagent",
            content="这是流式回复",
        )


class ErroringAgentRuntime:
    def stream_reply(self, *, session, user_message):
        yield AgentStreamEvent(event="started", run_id="run_error_001", mode="deepagent")
        raise AgentInvocationError(
            "函数调用失败：mock_tool 参数 invalid",
            error_type="function_error",
            stage="tool_call",
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
            self.assertEqual(
                payload["user_message"]["content"],
                "请帮我确认消息流是否正常",
            )
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
            self.assertEqual(
                runtime.calls,
                [(detail.meta.thread_id, "请帮我确认消息流是否正常")],
            )

    def test_stream_message_sends_sse_and_persists_messages(self) -> None:
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
                create_response = client.post("/api/v1/sessions", json={"title": "流式测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]

                with client.stream(
                    "POST",
                    f"/api/v1/sessions/{session_id}/messages/stream",
                    json={"content": "请流式回复"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

            events = _parse_sse_events(body)
            self.assertEqual(
                [event_name for event_name, _payload in events],
                [
                    "user_message",
                    "started",
                    "compression_started",
                    "compression_completed",
                    "token",
                    "token",
                    "done",
                ],
            )
            self.assertEqual(events[0][1]["user_message"]["content"], "请流式回复")
            self.assertEqual(events[2][1]["message"], "上下文较长，正在整理早期内容。")
            self.assertEqual(events[2][1]["details"]["phase"], "started")
            self.assertEqual(events[3][1]["message"], "上下文已自动压缩。")
            self.assertEqual(events[3][1]["details"]["phase"], "completed")
            self.assertEqual(events[2][1]["details"]["summarized_messages"], 3)
            self.assertEqual(events[4][1]["delta"], "这是")
            self.assertEqual(events[5][1]["delta"], "流式回复")
            self.assertEqual(events[-1][1]["assistant_message"]["content"], "这是流式回复")
            self.assertEqual(events[-1][1]["run_id"], "run_stream_001")

            detail = service.get_session(identity, session_id)
            self.assertEqual(
                [message.content for message in detail.messages],
                ["请流式回复", "这是流式回复"],
            )
            self.assertEqual(runtime.calls, [(detail.meta.thread_id, "请流式回复")])

    def test_stream_message_sends_error_event_without_assistant_persist(self) -> None:
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
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ErroringAgentRuntime()

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "报错测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]

                with client.stream(
                    "POST",
                    f"/api/v1/sessions/{session_id}/messages/stream",
                    json={"content": "调用一个会失败的函数"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    body = "".join(response.iter_text())

            events = _parse_sse_events(body)
            self.assertEqual(
                [event_name for event_name, _payload in events],
                ["user_message", "started", "error"],
            )
            self.assertEqual(events[-1][1]["error_type"], "function_error")
            self.assertEqual(events[-1][1]["stage"], "tool_call")
            self.assertIn("mock_tool 参数 invalid", events[-1][1]["detail"])

            detail = service.get_session(identity, session_id)
            self.assertEqual([message.role for message in detail.messages], ["user"])


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        event_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            if line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


if __name__ == "__main__":
    unittest.main()

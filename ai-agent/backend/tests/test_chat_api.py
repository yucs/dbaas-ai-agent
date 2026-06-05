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
from dbass_ai_agent.api.deps import (
    get_agent_runtime,
    get_app_settings,
    get_current_identity,
    get_session_service,
)
from dbass_ai_agent.api.routes_chat import router as chat_router
from dbass_ai_agent.api.routes_sessions import router as sessions_router
from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.sessions.approval_store import ApprovalStore
from dbass_ai_agent.sessions.index_store import IndexStore
from dbass_ai_agent.sessions.message_store import MessageStore
from dbass_ai_agent.sessions.operation_store import OperationStore
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.service import SessionService
from dbass_ai_agent.sessions.task_store import TaskStore
from dbass_ai_agent.sessions.thread_binding import ThreadBinding


class StubAgentRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate_reply(self, *, identity, session, user_message):
        self.calls.append((session.thread_id, user_message.content))
        return AgentReply(
            run_id="run_test_001",
            content="这是回归测试回复",
            mode="deepagent",
        )

    def stream_reply(self, *, identity, session, user_message):
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
    def generate_reply(self, *, identity, session, user_message):
        raise AgentInvocationError(
            "模型服务调用失败：mock provider unavailable",
            error_type="provider_error",
            stage="invoke",
        )

    def stream_reply(self, *, identity, session, user_message):
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
                    operation_store=OperationStore(),
                    task_store=TaskStore(),
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

    def test_send_message_persists_ai_agent_message_on_agent_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = SessionService(
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
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: ErroringAgentRuntime()

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "普通报错测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]

                message_response = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "普通调用也会失败"},
                )

            self.assertEqual(message_response.status_code, 502)
            detail = service.get_session(identity, session_id)
            self.assertEqual([message.role for message in detail.messages], ["user", "ai-agent"])
            self.assertIn("本轮 AI Agent 调用失败", detail.messages[-1].content)
            self.assertIn("mock provider unavailable", detail.messages[-1].content)
            self.assertNotIn("阶段：", detail.messages[-1].content)
            self.assertNotIn("类型：", detail.messages[-1].content)
            self.assertNotIn("运行编号：", detail.messages[-1].content)
            self.assertNotIn("排障编号：", detail.messages[-1].content)

    def test_send_message_rejects_content_over_configured_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = SessionService(
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
            runtime = StubAgentRuntime()
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime
            app.dependency_overrides[get_app_settings] = lambda: Settings(message_max_chars=5)

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "长度限制测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]

                message_response = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "超过五个字符"},
                )

            self.assertEqual(message_response.status_code, 422)
            self.assertEqual(message_response.json()["detail"], "消息长度不能超过 5 字符。")
            self.assertEqual(runtime.calls, [])
            self.assertEqual(service.get_session(identity, session_id).messages, [])

    def test_send_message_rejects_session_role_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            current_identity = {
                "value": Identity(user_id="alice", role="user", user="alice"),
            }
            service = SessionService(
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
            runtime = StubAgentRuntime()
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: current_identity["value"]
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "身份变化测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]
                same_identity_list = client.get("/api/v1/sessions")
                self.assertEqual(same_identity_list.status_code, 200)
                self.assertEqual(len(same_identity_list.json()["items"]), 1)
                self.assertEqual(same_identity_list.json()["stale_identity_items"], [])
                current_identity["value"] = Identity(user_id="alice", role="admin", user=None)

                changed_identity_list = client.get("/api/v1/sessions")
                self.assertEqual(changed_identity_list.status_code, 200)
                message_response = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "现在用管理员身份继续这个会话"},
                )

            stale_items = changed_identity_list.json()["stale_identity_items"]
            self.assertEqual(changed_identity_list.json()["items"], [])
            self.assertEqual(len(stale_items), 1)
            self.assertEqual(stale_items[0]["session_id"], session_id)
            self.assertEqual(stale_items[0]["role"], "user")
            self.assertEqual(stale_items[0]["user"], "alice")
            self.assertTrue(stale_items[0]["cleanup_only"])
            self.assertEqual(stale_items[0]["reason"], "session_identity_changed")
            self.assertEqual(message_response.status_code, 409)
            self.assertEqual(message_response.json()["detail"]["error_type"], "session_identity_changed")
            self.assertEqual(runtime.calls, [])
            original_identity = Identity(user_id="alice", role="user", user="alice")
            self.assertEqual(service.get_session(original_identity, session_id).messages, [])
            self.assertEqual(service.list_stale_identity_sessions(current_identity["value"])[0].session_id, session_id)
            self.assertEqual(len(service.list_sessions(original_identity)), 1)

    def test_send_message_rejects_user_scope_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            current_identity = {
                "value": Identity(user_id="alice", role="user", user="payment-team"),
            }
            service = SessionService(
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
            runtime = StubAgentRuntime()
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: current_identity["value"]
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "用户范围变化测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]
                current_identity["value"] = Identity(user_id="alice", role="user", user="another-team")

                message_response = client.post(
                    f"/api/v1/sessions/{session_id}/messages",
                    json={"content": "继续这个会话"},
                )

            self.assertEqual(message_response.status_code, 409)
            self.assertEqual(message_response.json()["detail"]["error_type"], "session_identity_changed")
            self.assertEqual(runtime.calls, [])

    def test_delete_session_allows_cleanup_after_role_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            current_identity = {
                "value": Identity(user_id="alice", role="user", user="alice"),
            }
            service = SessionService(
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
            runtime = StubAgentRuntime()
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: current_identity["value"]
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = _unexpected_agent_runtime
            app.dependency_overrides[get_app_settings] = lambda: Settings(
                checkpoint_db=Path(tmpdir) / "runtime" / "checkpoints.sqlite"
            )

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "旧身份清理测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]
                current_identity["value"] = Identity(user_id="alice", role="admin", user=None)

                delete_response = client.delete(f"/api/v1/sessions/{session_id}")

            self.assertEqual(delete_response.status_code, 200)
            self.assertEqual(delete_response.json()["session_id"], session_id)
            original_identity = Identity(user_id="alice", role="user", user="alice")
            with self.assertRaises(Exception):
                service.get_session_for_cleanup(original_identity, session_id)
            self.assertFalse((Path(tmpdir) / "alice").exists())

    def test_delete_session_keeps_user_directory_when_other_sessions_remain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="alice", role="user", user="alice")
            service = SessionService(
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
            first = service.create_session(identity, title="第一个会话")
            second = service.create_session(identity, title="第二个会话")

            deleted_session_id = service.delete_session(identity, first.meta.session_id)

            self.assertEqual(deleted_session_id, first.meta.session_id)
            self.assertFalse((Path(tmpdir) / "alice" / "sessions" / first.meta.session_id).exists())
            self.assertTrue((Path(tmpdir) / "alice").exists())
            self.assertTrue((Path(tmpdir) / "alice" / "sessions" / second.meta.session_id).exists())
            self.assertEqual(
                [item.session_id for item in service.list_sessions(identity)],
                [second.meta.session_id],
            )

    def test_stream_message_rejects_blank_content_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = SessionService(
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
            runtime = StubAgentRuntime()
            app = FastAPI()
            app.include_router(sessions_router)
            app.include_router(chat_router)
            app.dependency_overrides[get_current_identity] = lambda: identity
            app.dependency_overrides[get_session_service] = lambda: service
            app.dependency_overrides[get_agent_runtime] = lambda: runtime

            with TestClient(app) as client:
                create_response = client.post("/api/v1/sessions", json={"title": "空消息测试"})
                self.assertEqual(create_response.status_code, 200)
                session_id = create_response.json()["session"]["meta"]["session_id"]

                message_response = client.post(
                    f"/api/v1/sessions/{session_id}/messages/stream",
                    json={"content": "   "},
                )

            self.assertEqual(message_response.status_code, 422)
            self.assertEqual(message_response.json()["detail"], "消息内容不能为空。")
            self.assertEqual(runtime.calls, [])
            self.assertEqual(service.get_session(identity, session_id).messages, [])

    def test_stream_message_sends_sse_and_persists_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = SessionService(
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

                with client.stream(
                    "POST",
                    f"/api/v1/sessions/{session_id}/messages/stream",
                    json={"content": "你好"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    second_body = "".join(response.iter_text())

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
            self.assertIsNone(events[2][1]["system_message"])
            self.assertEqual(events[3][1]["message"], "上下文已自动压缩。")
            self.assertEqual(events[3][1]["details"]["phase"], "completed")
            self.assertEqual(events[3][1]["system_message"]["role"], "system")
            self.assertEqual(events[3][1]["system_message"]["content"], "上下文已自动压缩。")
            self.assertEqual(events[2][1]["details"]["summarized_messages"], 3)
            self.assertEqual(events[4][1]["delta"], "这是")
            self.assertEqual(events[5][1]["delta"], "流式回复")
            self.assertEqual(events[-1][1]["assistant_message"]["content"], "这是流式回复")
            self.assertEqual(events[-1][1]["run_id"], "run_stream_001")

            second_events = _parse_sse_events(second_body)
            self.assertEqual(second_events[3][0], "compression_completed")
            self.assertIsNone(second_events[3][1]["system_message"])

            detail = service.get_session(identity, session_id)
            self.assertEqual(
                [message.content for message in detail.messages],
                [
                    "请流式回复",
                    "上下文已自动压缩。",
                    "这是流式回复",
                    "你好",
                    "这是流式回复",
                ],
            )
            self.assertEqual(
                [message.role for message in detail.messages],
                ["user", "system", "assistant", "user", "assistant"],
            )
            self.assertEqual(
                runtime.calls,
                [
                    (detail.meta.thread_id, "请流式回复"),
                    (detail.meta.thread_id, "你好"),
                ],
            )

    def test_stream_message_sends_error_event_and_persists_ai_agent_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = Identity(user_id="admin", role="admin", user="Admin")
            service = SessionService(
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
            self.assertEqual(events[-1][1]["run_id"], "run_error_001")
            self.assertEqual(events[-1][1]["ai_agent_message"]["role"], "ai-agent")
            self.assertIn(
                "本轮 AI Agent 调用失败",
                events[-1][1]["ai_agent_message"]["content"],
            )
            self.assertIn("mock_tool 参数 invalid", events[-1][1]["ai_agent_message"]["content"])
            self.assertNotIn("阶段：", events[-1][1]["ai_agent_message"]["content"])
            self.assertNotIn("类型：", events[-1][1]["ai_agent_message"]["content"])
            self.assertNotIn("运行编号：", events[-1][1]["ai_agent_message"]["content"])
            self.assertNotIn("排障编号：", events[-1][1]["ai_agent_message"]["content"])

            detail = service.get_session(identity, session_id)
            self.assertEqual([message.role for message in detail.messages], ["user", "ai-agent"])
            self.assertEqual(
                detail.messages[-1].content,
                events[-1][1]["ai_agent_message"]["content"],
            )


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


def _unexpected_agent_runtime():
    raise AssertionError("DeepAgent runtime should not be initialized")


if __name__ == "__main__":
    unittest.main()

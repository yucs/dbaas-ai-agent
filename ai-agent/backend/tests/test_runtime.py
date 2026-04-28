from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.runtime import DeepAgentRuntime  # noqa: E402
from dbass_ai_agent.sessions.models import ChatMessage, SessionMeta  # noqa: E402


class DeepAgentRuntimeDbaasTests(unittest.TestCase):
    def test_dbaas_message_invokes_real_runtime_path(self) -> None:
        runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
        calls: list[tuple[str, str]] = []

        def invoke_agent(thread_id: str, prompt: str) -> str:
            calls.append((thread_id, prompt))
            return "已查询 dbaas-server 并返回结果"

        runtime._invoke_agent = invoke_agent
        session = _session_meta()
        user_message = _user_message("请查询 mysql-xf2 当前 CPU 和内存状态")

        reply = runtime.generate_reply(session=session, user_message=user_message)

        self.assertEqual(reply.content, "已查询 dbaas-server 并返回结果")
        self.assertIsNone(reply.warning)
        self.assertEqual(calls, [(session.thread_id, user_message.content)])

    def test_dbaas_stream_message_uses_stream_path_without_mock_warning(self) -> None:
        runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
        calls: list[tuple[str, str]] = []

        def stream_agent_text(thread_id: str, prompt: str):
            calls.append((thread_id, prompt))
            yield "已通过 DBAAS 工具"
            yield "完成查询"

        runtime._stream_agent_text = stream_agent_text
        session = _session_meta()
        user_message = _user_message("扩容 mysql-xf2 到 16C64G 前先检查集群状态")

        events = list(runtime.stream_reply(session=session, user_message=user_message))

        self.assertEqual([event.event for event in events], ["started", "token", "token", "completed"])
        self.assertTrue(all(event.warning is None for event in events))
        self.assertEqual(events[-1].content, "已通过 DBAAS 工具完成查询")
        self.assertEqual(calls, [(session.thread_id, user_message.content)])


def _session_meta() -> SessionMeta:
    now = datetime.now(UTC)
    return SessionMeta(
        session_id="sess_test",
        user_id="admin",
        role="admin",
        thread_id="thread_test",
        title="runtime test",
        created_at=now,
        updated_at=now,
    )


def _user_message(content: str) -> ChatMessage:
    return ChatMessage(
        message_id="msg_test",
        role="user",
        content=content,
        created_at=datetime.now(UTC),
    )


if __name__ == "__main__":
    unittest.main()

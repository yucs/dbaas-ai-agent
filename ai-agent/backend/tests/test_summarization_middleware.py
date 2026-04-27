from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.language_models.model_profile import ModelProfile
from langgraph.checkpoint.memory import InMemorySaver


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


SUMMARY_TEXT = """## SESSION INTENT
确认长会话压缩是否正常工作。

## SUMMARY
第一轮问答已经完成，后续继续保留最近一轮上下文即可。

## ARTIFACTS
None

## NEXT STEPS
继续下一轮提问并验证摘要是否替代旧消息。
"""


class SummarizationMiddlewareTests(unittest.TestCase):
    def _build_agent(self, root_dir: str):
        model = FakeListChatModel(
            responses=[
                "第一轮正常回复",
                SUMMARY_TEXT,
                "压缩后继续回复",
            ],
            profile=ModelProfile(max_input_tokens=512),
        )
        backend = FilesystemBackend(root_dir=root_dir, virtual_mode=True)
        middleware = SummarizationMiddleware(
            model=model,
            backend=backend,
            trigger=("messages", 3),
            keep=("messages", 1),
        )
        agent = create_agent(
            model,
            middleware=[middleware],
            checkpointer=InMemorySaver(),
        )
        return agent, middleware

    def test_summarization_creates_event_and_offloads_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent, _middleware = self._build_agent(tmpdir)
            thread_id = "compress-thread"

            agent.invoke(
                {"messages": [{"role": "user", "content": "第一轮问题"}]},
                config={"configurable": {"thread_id": thread_id}},
            )
            agent.invoke(
                {"messages": [{"role": "user", "content": "第二轮问题"}]},
                config={"configurable": {"thread_id": thread_id}},
            )

            state = agent.get_state(config={"configurable": {"thread_id": thread_id}})
            event = state.values.get("_summarization_event")

            self.assertIsNotNone(event)
            self.assertEqual(event["cutoff_index"], 2)
            self.assertEqual(event["file_path"], f"/conversation_history/{thread_id}.md")
            self.assertIn("第一轮问答已经完成", event["summary_message"].content)
            self.assertEqual(
                event["summary_message"].additional_kwargs.get("lc_source"),
                "summarization",
            )

            history_file = Path(tmpdir) / "conversation_history" / f"{thread_id}.md"
            self.assertTrue(history_file.exists())
            history = history_file.read_text(encoding="utf-8")
            self.assertIn("Human: 第一轮问题", history)
            self.assertIn("AI: 第一轮正常回复", history)

    def test_summarization_event_replaces_old_messages_in_effective_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent, middleware = self._build_agent(tmpdir)
            thread_id = "compress-thread"

            agent.invoke(
                {"messages": [{"role": "user", "content": "第一轮问题"}]},
                config={"configurable": {"thread_id": thread_id}},
            )
            agent.invoke(
                {"messages": [{"role": "user", "content": "第二轮问题"}]},
                config={"configurable": {"thread_id": thread_id}},
            )

            state = agent.get_state(config={"configurable": {"thread_id": thread_id}})
            effective_messages = middleware._apply_event_to_messages(
                state.values["messages"],
                state.values["_summarization_event"],
            )

            self.assertEqual(len(effective_messages), 3)
            self.assertEqual(
                effective_messages[0].additional_kwargs.get("lc_source"),
                "summarization",
            )
            self.assertIn("继续下一轮提问", effective_messages[0].content)
            self.assertEqual(effective_messages[1].content, "第二轮问题")
            self.assertEqual(effective_messages[2].content, "压缩后继续回复")


if __name__ == "__main__":
    unittest.main()

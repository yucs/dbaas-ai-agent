from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.language_models.model_profile import ModelProfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.factory import (  # noqa: E402
    _build_logged_summarization_middleware_class,
    _build_chat_model,
    build_runtime_artifacts,
    build_summarization_middleware_factory,
    patch_deepagents_summarization_factory,
)
from dbass_ai_agent.agent.compression_events import capture_compression_notices  # noqa: E402
from dbass_ai_agent.config import Settings  # noqa: E402


class BuildRuntimeArtifactsTests(unittest.TestCase):
    def test_create_deep_agent_uses_configured_summarization_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            checkpoint_db = Path(tmpdir) / "runtime" / "checkpoints.sqlite"
            settings = Settings(
                model="demo-model",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                checkpoint_db=checkpoint_db,
                system_prompt_path=Path(tmpdir) / "system.md",
            )
            main_model = Mock(name="chat_model")
            summary_model = Mock(name="summary_model")
            summary_factory = Mock(name="summary_factory")

            with (
                patch("dbass_ai_agent.agent.factory.sqlite3.connect", return_value=Mock()) as connect_mock,
                patch("dbass_ai_agent.agent.factory.httpx.Client", return_value=Mock()) as client_mock,
                patch(
                    "dbass_ai_agent.agent.factory.httpx.AsyncClient",
                    return_value=Mock(),
                ) as async_client_mock,
                patch(
                    "dbass_ai_agent.agent.factory.load_system_prompt",
                    return_value="system prompt",
                ),
                patch(
                    "dbass_ai_agent.agent.factory._build_chat_model",
                    side_effect=[main_model, summary_model],
                ) as build_chat_model_mock,
                patch(
                    "dbass_ai_agent.agent.factory.build_summarization_middleware_factory",
                    return_value=summary_factory,
                ) as build_summary_factory_mock,
                patch(
                    "dbass_ai_agent.agent.factory.patch_deepagents_summarization_factory",
                    return_value=contextlib.nullcontext(),
                ) as patch_summary_factory_mock,
                patch("deepagents.create_deep_agent", return_value=Mock(name="agent")) as create_agent_mock,
                patch(
                    "langgraph.checkpoint.sqlite.SqliteSaver",
                    return_value=Mock(name="checkpointer"),
                ) as saver_mock,
            ):
                artifacts = build_runtime_artifacts(settings)

            self.assertIs(artifacts.agent, create_agent_mock.return_value)
            self.assertIs(artifacts.connection, connect_mock.return_value)
            self.assertIs(artifacts.http_client, client_mock.return_value)
            self.assertIs(artifacts.http_async_client, async_client_mock.return_value)

            self.assertEqual(build_chat_model_mock.call_count, 2)
            build_summary_factory_mock.assert_called_once_with(
                settings,
                summary_model=summary_model,
            )
            patch_summary_factory_mock.assert_called_once_with(summary_factory)

            create_agent_mock.assert_called_once()
            kwargs = create_agent_mock.call_args.kwargs
            self.assertEqual(set(kwargs), {"model", "checkpointer", "system_prompt"})
            self.assertNotIn("middleware", kwargs)
            self.assertIs(kwargs["model"], main_model)
            self.assertIs(kwargs["checkpointer"], saver_mock.return_value)
            self.assertEqual(kwargs["system_prompt"], "system prompt")

    def test_build_chat_model_can_disable_thinking_for_provider_specific_compat(self) -> None:
        settings = Settings(
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            api_key="test-key",
            thinking_enabled=False,
        )
        http_client = Mock(name="http_client")
        http_async_client = Mock(name="http_async_client")

        with patch("langchain_openai.ChatOpenAI") as chat_openai_mock:
            _build_chat_model(
                settings,
                http_client=http_client,
                http_async_client=http_async_client,
                max_completion_tokens=4096,
            )

        chat_openai_mock.assert_called_once_with(
            model="deepseek-v4-pro",
            api_key="test-key",
            base_url="https://api.deepseek.com/v1",
            temperature=0.2,
            max_completion_tokens=4096,
            extra_body={"thinking": {"type": "disabled"}},
            http_client=http_client,
            http_async_client=http_async_client,
            http_socket_options=(),
        )

    def test_build_chat_model_omits_thinking_toggle_by_default(self) -> None:
        settings = Settings(
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            api_key="test-key",
        )
        http_client = Mock(name="http_client")
        http_async_client = Mock(name="http_async_client")

        with patch("langchain_openai.ChatOpenAI") as chat_openai_mock:
            _build_chat_model(
                settings,
                http_client=http_client,
                http_async_client=http_async_client,
                max_completion_tokens=2048,
            )

        chat_openai_mock.assert_called_once_with(
            model="deepseek-chat",
            api_key="test-key",
            base_url="https://api.deepseek.com/v1",
            temperature=0.2,
            max_completion_tokens=2048,
            extra_body=None,
            http_client=http_client,
            http_async_client=http_async_client,
            http_socket_options=(),
        )


class SummarizationFactoryTests(unittest.TestCase):
    def test_build_summarization_middleware_factory_uses_prompt_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "compression.md"
            prompt_path.write_text("custom compression prompt", encoding="utf-8")
            settings = Settings(
                compression_prompt_path=prompt_path,
                compression_enabled=True,
                soft_trigger_tokens=4321,
                keep_recent_messages=9,
            )
            summary_model = FakeListChatModel(
                responses=["summary"],
                profile=ModelProfile(max_input_tokens=1024),
            )

            factory = build_summarization_middleware_factory(
                settings,
                summary_model=summary_model,
            )
            middleware = factory(object(), backend="backend")

            self.assertIsInstance(middleware, SummarizationMiddleware)
            self.assertEqual(middleware._lc_helper.trigger, ("tokens", 4321))
            self.assertEqual(middleware._lc_helper.keep, ("messages", 9))
            self.assertEqual(middleware._lc_helper.summary_prompt, "custom compression prompt")
            self.assertIs(middleware.model, summary_model)
            self.assertEqual(middleware._backend, "backend")

    def test_patch_deepagents_summarization_factory_restores_original_factory(self) -> None:
        import deepagents.graph as deepagents_graph

        original_factory = deepagents_graph.create_summarization_middleware
        replacement = Mock(name="replacement")

        with patch_deepagents_summarization_factory(replacement):
            self.assertIs(deepagents_graph.create_summarization_middleware, replacement)

        self.assertIs(deepagents_graph.create_summarization_middleware, original_factory)

    def test_build_summarization_middleware_factory_can_disable_compression(self) -> None:
        settings = Settings(compression_enabled=False)
        summary_model = FakeListChatModel(
            responses=["summary"],
            profile=ModelProfile(max_input_tokens=1024),
        )

        factory = build_summarization_middleware_factory(
            settings,
            summary_model=summary_model,
        )
        middleware = factory(object(), backend="backend")

        self.assertNotIsInstance(middleware, SummarizationMiddleware)

    def test_logged_summarization_middleware_emits_info_logs(self) -> None:
        LoggedSummarizationMiddleware = _build_logged_summarization_middleware_class()
        summary_model = FakeListChatModel(
            responses=["压缩摘要"],
            profile=ModelProfile(max_input_tokens=1024),
        )
        middleware = LoggedSummarizationMiddleware(
            model=summary_model,
            backend="backend",
            trigger=("tokens", 100),
            keep=("messages", 2),
            summary_prompt="custom compression prompt",
        )

        messages_to_summarize = [
            HumanMessage(content="第一轮问题"),
            AIMessage(content="第一轮回复"),
        ]

        with self.assertLogs("dbass_ai_agent.agent.factory", level="INFO") as captured:
            summary = middleware._create_summary(messages_to_summarize)

        self.assertEqual(summary, "压缩摘要")
        self.assertTrue(
            any("会话上下文开始压缩" in line for line in captured.output),
            captured.output,
        )
        self.assertTrue(
            any("会话上下文已压缩" in line for line in captured.output),
            captured.output,
        )
        self.assertTrue(
            any("会话上下文压缩摘要" in line for line in captured.output),
            captured.output,
        )
        self.assertTrue(
            any("summarized_messages=2" in line for line in captured.output),
            captured.output,
        )
        self.assertTrue(
            any("history_path=" in line for line in captured.output),
            captured.output,
        )
        self.assertTrue(
            any("summary=压缩摘要" in line for line in captured.output),
            captured.output,
        )

    def test_logged_summarization_middleware_publishes_compression_notice(self) -> None:
        LoggedSummarizationMiddleware = _build_logged_summarization_middleware_class()
        summary_model = FakeListChatModel(
            responses=["压缩摘要"],
            profile=ModelProfile(max_input_tokens=1024),
        )
        middleware = LoggedSummarizationMiddleware(
            model=summary_model,
            backend="backend",
            trigger=("tokens", 100),
            keep=("messages", 2),
            summary_prompt="custom compression prompt",
        )
        messages_to_summarize = [
            HumanMessage(content="第一轮问题"),
            AIMessage(content="第一轮回复"),
        ]
        notices = []

        with capture_compression_notices(notices.append):
            middleware._create_summary(messages_to_summarize)

        self.assertEqual(len(notices), 2)
        self.assertEqual([notice.phase for notice in notices], ["started", "completed"])
        self.assertEqual(notices[0].summarized_messages, 2)
        self.assertEqual(notices[0].keep, "('messages', 2)")
        self.assertEqual(notices[0].trigger, "('tokens', 100)")
        self.assertIsNone(notices[0].summary_chars)
        self.assertEqual(notices[1].summary_chars, 4)


if __name__ == "__main__":
    unittest.main()

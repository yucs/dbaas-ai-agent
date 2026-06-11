from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.language_models.model_profile import ModelProfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.factory import (  # noqa: E402
    AgentFactoryError,
    DEEPAGENTS_BUILTIN_TOOLS_DISABLED_PROMPT,
    DEEPAGENTS_BUILTIN_TOOL_NAMES,
    DbaasToolAllowlistMiddleware,
    _build_logged_summarization_middleware_class,
    _build_chat_model,
    _create_runtime_agent,
    _dbaas_tool_allowlist_middleware,
    _interrupt_on_for_tools,
    build_runtime_artifacts,
    build_summarization_middleware_factory,
    patch_deepagents_summarization_factory,
)
from dbass_ai_agent.agent.compression_events import capture_compression_notices  # noqa: E402
from dbass_ai_agent.agent.prompt import load_compression_prompt, load_system_prompt  # noqa: E402
from dbass_ai_agent.config import APP_ROOT, Settings  # noqa: E402


def _tool_name(tool: object) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class _FakeModelRequest:
    def __init__(
        self,
        tools: list[object],
        system_message: SystemMessage | None = None,
    ) -> None:
        self.tools = tools
        self.system_message = system_message

    def override(
        self,
        *,
        tools: list[object],
        system_message: SystemMessage | None = None,
    ) -> "_FakeModelRequest":
        return _FakeModelRequest(tools, system_message)


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
                    side_effect=lambda _path, role: f"system prompt {role}",
                ) as load_system_prompt_mock,
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

            self.assertEqual(set(artifacts.agents), {"user", "admin"})
            self.assertIs(artifacts.connection, connect_mock.return_value)
            self.assertIs(artifacts.http_client, client_mock.return_value)
            self.assertIs(artifacts.http_async_client, async_client_mock.return_value)

            self.assertEqual(build_chat_model_mock.call_count, 2)
            build_summary_factory_mock.assert_called_once_with(
                settings,
                summary_model=summary_model,
            )
            self.assertEqual(patch_summary_factory_mock.call_count, 2)
            patch_summary_factory_mock.assert_called_with(summary_factory)

            self.assertEqual(create_agent_mock.call_count, 2)
            self.assertEqual(
                [call.args for call in load_system_prompt_mock.call_args_list],
                [((settings.system_prompt_path, "user")), ((settings.system_prompt_path, "admin"))],
            )
            kwargs = create_agent_mock.call_args_list[0].kwargs
            self.assertEqual(
                set(kwargs),
                {
                    "model",
                    "tools",
                    "middleware",
                    "checkpointer",
                    "system_prompt",
                    "interrupt_on",
                },
            )
            self.assertIs(kwargs["model"], main_model)
            expected_common_tools = {
                "query_dbaas_service_data_tool",
                "describe_dbaas_schema_tool",
                "describe_service_backup_capability_tool",
                "describe_service_image_upgrade_capability_tool",
                "describe_unit_metric_catalog_tool",
                "query_unit_latest_metric_data_tool",
                "query_unit_metric_history_tool",
                "get_current_time_tool",
                "precheck_service_resource_update_tool",
                "precheck_service_storage_update_tool",
                "update_service_resource_tool",
                "update_service_storage_tool",
                "create_service_image_upgrade_task_tool",
                "create_service_backup_task_tool",
                "get_dbaas_task_tool",
                "list_current_session_tasks_tool",
            }
            self.assertTrue(
                expected_common_tools.issubset({tool.name for tool in kwargs["tools"]})
            )
            self.assertTrue(
                expected_common_tools.issubset(
                    {tool.name for tool in create_agent_mock.call_args_list[1].kwargs["tools"]}
                )
            )
            self.assertEqual(
                set(kwargs["interrupt_on"]),
                {
                    "update_service_resource_tool",
                    "update_service_storage_tool",
                    "create_service_image_upgrade_task_tool",
                    "create_service_backup_task_tool",
                },
            )
            self.assertEqual(len(kwargs["middleware"]), 1)
            self.assertIsInstance(kwargs["middleware"][0], DbaasToolAllowlistMiddleware)
            self.assertIs(kwargs["checkpointer"], saver_mock.return_value)
            self.assertEqual(
                [call.kwargs["system_prompt"] for call in create_agent_mock.call_args_list],
                ["system prompt user", "system prompt admin"],
            )

    def test_interrupt_on_for_tools_filters_missing_role_tools(self) -> None:
        with patch(
            "dbass_ai_agent.agent.factory.build_interrupt_on_config",
            return_value={
                "user_write_tool": {"allowed_decisions": ["approve", "reject"]},
                "admin_write_tool": {"allowed_decisions": ["approve", "reject"]},
            },
        ):
            result = _interrupt_on_for_tools(
                [
                    SimpleNamespace(name="user_write_tool"),
                    SimpleNamespace(name="read_only_tool"),
                ]
            )

        self.assertEqual(set(result), {"user_write_tool"})

    def test_dbaas_tool_allowlist_filters_deepagents_builtin_tools(self) -> None:
        allowed_tools = [
            SimpleNamespace(name="query_dbaas_service_data_tool"),
            SimpleNamespace(name="query_unit_metric_history_tool"),
        ]
        middleware = _dbaas_tool_allowlist_middleware(allowed_tools)
        request = _FakeModelRequest(
            [
                *allowed_tools,
                *(SimpleNamespace(name=name) for name in DEEPAGENTS_BUILTIN_TOOL_NAMES),
                {"name": "task"},
                {"name": "query_unit_metric_history_tool"},
                SimpleNamespace(name=None),
            ]
        )

        filtered_names = middleware.wrap_model_call(
            request,
            lambda filtered_request: [_tool_name(tool) for tool in filtered_request.tools],
        )

        self.assertEqual(
            filtered_names,
            [
                "query_dbaas_service_data_tool",
                "query_unit_metric_history_tool",
                "query_unit_metric_history_tool",
            ],
        )
        self.assertTrue(DEEPAGENTS_BUILTIN_TOOL_NAMES.isdisjoint(filtered_names))

    def test_dbaas_tool_allowlist_appends_disabled_builtin_notice(self) -> None:
        middleware = _dbaas_tool_allowlist_middleware(
            [SimpleNamespace(name="query_dbaas_service_data_tool")]
        )
        request = _FakeModelRequest(
            [SimpleNamespace(name="query_dbaas_service_data_tool")],
            system_message=SystemMessage(content="base prompt"),
        )

        system_text = middleware.wrap_model_call(
            request,
            lambda filtered_request: filtered_request.system_message.text,
        )

        self.assertIn("base prompt", system_text)
        self.assertIn(DEEPAGENTS_BUILTIN_TOOLS_DISABLED_PROMPT, system_text)

    def test_load_system_prompt_appends_role_extend_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_dir = Path(tmpdir)
            system_path = prompt_dir / "system.md"
            system_path.write_text("common rules", encoding="utf-8")
            (prompt_dir / "user_extend_system_prompt.md").write_text("user rules", encoding="utf-8")
            (prompt_dir / "admin_extend_system_prompt.md").write_text("admin rules", encoding="utf-8")

            self.assertEqual(load_system_prompt(system_path, "user"), "common rules\n\nuser rules")
            self.assertEqual(load_system_prompt(system_path, "admin"), "common rules\n\nadmin rules")

    def test_real_user_prompt_does_not_expose_admin_host_tool(self) -> None:
        system_path = APP_ROOT / "backend" / "prompts" / "system.md"

        user_prompt = load_system_prompt(system_path, "user")
        admin_prompt = load_system_prompt(system_path, "admin")

        self.assertNotIn("query_dbaas_host_data_tool", user_prompt)
        self.assertNotIn('kind="hosts"', user_prompt)
        self.assertIn("query_dbaas_host_data_tool", admin_prompt)
        self.assertIn('kind="hosts"', admin_prompt)

    def test_load_system_prompt_requires_role_extend_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            system_path = Path(tmpdir) / "system.md"
            system_path.write_text("common rules", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                load_system_prompt(system_path, "user")

    def test_load_system_prompt_requires_common_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_dir = Path(tmpdir)
            system_path = prompt_dir / "system.md"
            (prompt_dir / "user_extend_system_prompt.md").write_text("user rules", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                load_system_prompt(system_path, "user")

    def test_load_compression_prompt_reads_required_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = Path(tmpdir) / "compression.md"
            prompt_path.write_text("compression rules", encoding="utf-8")

            self.assertEqual(load_compression_prompt(prompt_path), "compression rules")

    def test_load_compression_prompt_requires_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(FileNotFoundError):
                load_compression_prompt(Path(tmpdir) / "compression.md")

    def test_create_runtime_agent_wraps_missing_role_prompt_as_factory_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            system_path = Path(tmpdir) / "system.md"
            system_path.write_text("common rules", encoding="utf-8")
            settings = Settings(system_prompt_path=system_path)

            with self.assertRaises(AgentFactoryError):
                _create_runtime_agent(
                    settings,
                    role="user",
                    create_deep_agent=Mock(),
                    model=Mock(),
                    checkpointer=Mock(),
                    summarization_factory=Mock(),
                )

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

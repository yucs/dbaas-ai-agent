from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, HumanMessage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.factory import (
    _build_chat_model,
    _build_logged_summarization_middleware_class,
    build_summarization_middleware_factory,
)
from dbass_ai_agent.config import DEFAULT_CONFIG_PATH, ConfigError, Settings
from dbass_ai_agent.agent.prompt import load_compression_prompt


class RealLLMSummarizationIntegrationTests(unittest.TestCase):
    def test_logged_summarization_middleware_invokes_real_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = _real_llm_settings(Path(tmpdir))
            with _model_clients() as (http_client, http_async_client):
                summary_model = _build_chat_model(
                    settings,
                    http_client=http_client,
                    http_async_client=http_async_client,
                    max_completion_tokens=settings.summary_max_tokens,
                )
                middleware_factory = build_summarization_middleware_factory(
                    settings,
                    summary_model=summary_model,
                )
                middleware = middleware_factory(object(), backend="backend")
                self.assertIsInstance(middleware, SummarizationMiddleware)

                LoggedSummarizationMiddleware = _build_logged_summarization_middleware_class()
                logged_middleware = LoggedSummarizationMiddleware(
                    model=summary_model,
                    backend="backend",
                    trigger=("tokens", 100),
                    keep=("messages", 2),
                    summary_prompt=load_compression_prompt(settings.compression_prompt_path),
                )
                messages_to_summarize = [
                    HumanMessage(content="帮我看一下 mysql-prod-01 当前状态，顺便评估能不能扩到 16C64G。"),
                    AIMessage(
                        content=(
                            "已通过 DBAAS 工具查询 mysql-prod-01：当前规格 8C32G，"
                            "主从延迟 0.4 秒，实例状态正常，具备进入扩容评估的前提。"
                        )
                    ),
                    HumanMessage(
                        content=(
                            "目标是 mysql-prod-01 从 8C32G 扩到 16C64G。"
                            "业务窗口是今晚 22:00-23:00，必须先确认主从延迟小于 1 秒，"
                            "并且不能重启实例。"
                        )
                    ),
                    AIMessage(
                        content=(
                            "已记录：资源 mysql-prod-01；目标规格 16C64G；当前用户声称规格 8C32G；"
                            "窗口今晚 22:00-23:00；前置约束是主从延迟小于 1 秒且不能重启。"
                            "已查询到当前主从延迟 0.4 秒，实例状态正常。"
                        )
                    ),
                    HumanMessage(content="另外如果检查发现延迟超过 1 秒，就不要扩容，只给我风险说明。"),
                    AIMessage(
                        content=(
                            "已记录条件：若后续检查主从延迟超过 1 秒，则不执行扩容，"
                            "只输出风险说明和建议。当前没有已审批动作。"
                        )
                    ),
                ]

                summary = logged_middleware._create_summary(messages_to_summarize)

        self.assertTrue(summary.strip())
        print(f"\n压缩后的内容:\n{summary}\n")


def _real_llm_settings(root: Path) -> Settings:
    try:
        settings = Settings.from_file(DEFAULT_CONFIG_PATH)
    except ConfigError as exc:
        raise unittest.SkipTest(
            f"缺少真实模型测试配置文件 {DEFAULT_CONFIG_PATH}: {exc}"
        ) from exc

    if not settings.real_llm_tests_enabled:
        raise unittest.SkipTest(
            "真实大模型测试未开启；如需执行，请在 config.toml 的 [tests] 中设置 "
            "real_llm_enabled = true。"
        )

    if not settings.model:
        raise unittest.SkipTest("真实大模型测试缺少 [model].model 配置。")
    if not settings.base_url:
        raise unittest.SkipTest("真实大模型测试缺少 [model].base_url 配置。")
    if not settings.api_key or settings.api_key == "replace-with-your-api-key":
        raise unittest.SkipTest("真实大模型测试缺少可用的 [model].api_key 配置。")

    return replace(
        settings,
        data_root=root / "users",
        runtime_root=root / "runtime",
        checkpoint_db=root / "runtime" / "checkpoints.sqlite",
        log_file=root / "logs" / "app.log",
        max_output_tokens=max(1, min(settings.max_output_tokens, 256)),
        summary_max_tokens=max(1, min(settings.summary_max_tokens, 256)),
    )


@contextmanager
def _model_clients() -> Iterator[tuple[httpx.Client, httpx.AsyncClient]]:
    http_client = httpx.Client(trust_env=False, timeout=60)
    http_async_client = httpx.AsyncClient(trust_env=False, timeout=60)
    try:
        yield http_client, http_async_client
    finally:
        http_client.close()
        asyncio.run(http_async_client.aclose())


if __name__ == "__main__":
    unittest.main()

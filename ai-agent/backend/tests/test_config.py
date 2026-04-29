from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.config import Settings


class SettingsFromFileTests(unittest.TestCase):
    def test_from_file_reads_toml_and_resolves_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_root = Path(tmpdir)
            config_path = config_root / "config.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [app]
                    name = "unit-test-app"

                    [server]
                    host = "0.0.0.0"
                    port = 9001

                    [chat]
                    message_max_chars = 12345

                    [paths]
                    data_root = "./var/users"
                    frontend_root = "./ui"
                    runtime_root = "./var/runtime"
                    checkpoint_db = "./var/runtime/state.sqlite"
                    system_prompt_path = "./prompts/system.md"
                    compression_prompt_path = "./prompts/compression.md"

                    [model]
                    provider_kind = "openai_compatible"
                    model = "demo-model"
                    base_url = "https://example.invalid/v1"
                    api_key = "test-key"
                    context_window = 65536
                    max_output_tokens = 4096
                    thinking_enabled = false

                    [compression]
                    enabled = false
                    soft_trigger_tokens = 50000
                    keep_recent_messages = 8
                    summary_max_tokens = 1024

                    [dbaas_server]
                    base_url = "http://127.0.0.1:9000"
                    request_timeout_seconds = 7

                    [dbaas_workspace]
                    dir = "./var/dbaas"
                    sync_interval_seconds = 5
                    ttl_seconds = 30
                    jq_timeout_seconds = 6
                    jq_max_preview_items = 25
                    jq_max_output_bytes = 2048

                    [logging]
                    level = "DEBUG"
                    log_file = "./logs/unit.log"
                    max_bytes = 2048
                    backup_count = 3
                    enable_console = false
                    log_request_body = true

                    [tests]
                    real_llm_enabled = true
                    """
                ).strip(),
                encoding="utf-8",
            )

            settings = Settings.from_file(config_path)

            self.assertEqual(settings.app_name, "unit-test-app")
            self.assertEqual(settings.host, "0.0.0.0")
            self.assertEqual(settings.port, 9001)
            self.assertEqual(settings.message_max_chars, 12345)
            self.assertEqual(settings.model, "demo-model")
            self.assertEqual(settings.base_url, "https://example.invalid/v1")
            self.assertEqual(settings.api_key, "test-key")
            self.assertEqual(settings.context_window, 65536)
            self.assertEqual(settings.max_output_tokens, 4096)
            self.assertFalse(settings.thinking_enabled)
            self.assertFalse(settings.compression_enabled)
            self.assertEqual(settings.soft_trigger_tokens, 50000)
            self.assertEqual(settings.keep_recent_messages, 8)
            self.assertEqual(settings.summary_max_tokens, 1024)
            self.assertEqual(settings.dbaas_server_base_url, "http://127.0.0.1:9000")
            self.assertEqual(settings.dbaas_request_timeout_seconds, 7)
            self.assertEqual(settings.dbaas_sync_interval_seconds, 5)
            self.assertEqual(settings.dbaas_ttl_seconds, 30)
            self.assertEqual(settings.dbaas_jq_timeout_seconds, 6)
            self.assertEqual(settings.dbaas_jq_max_preview_items, 25)
            self.assertEqual(settings.dbaas_jq_max_output_bytes, 2048)
            self.assertEqual(settings.log_level, "DEBUG")
            self.assertEqual(settings.log_max_bytes, 2048)
            self.assertEqual(settings.log_backup_count, 3)
            self.assertFalse(settings.log_enable_console)
            self.assertTrue(settings.log_request_body)
            self.assertTrue(settings.real_llm_tests_enabled)

            self.assertEqual(settings.data_root, (config_root / "var/users").resolve())
            self.assertEqual(settings.frontend_root, (config_root / "ui").resolve())
            self.assertEqual(settings.runtime_root, (config_root / "var/runtime").resolve())
            self.assertEqual(
                settings.checkpoint_db,
                (config_root / "var/runtime/state.sqlite").resolve(),
            )
            self.assertEqual(
                settings.system_prompt_path,
                (config_root / "prompts/system.md").resolve(),
            )
            self.assertEqual(
                settings.compression_prompt_path,
                (config_root / "prompts/compression.md").resolve(),
            )
            self.assertEqual(settings.dbaas_workspace_dir, (config_root / "var/dbaas").resolve())
            self.assertEqual(settings.log_file, (config_root / "logs/unit.log").resolve())


if __name__ == "__main__":
    unittest.main()

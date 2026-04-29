from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
MOCK_SERVER_ROOT = APP_ROOT.parent / "mock-server"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.agent.runtime import DeepAgentRuntime  # noqa: E402
from dbass_ai_agent.config import DEFAULT_CONFIG_PATH, ConfigError, Settings  # noqa: E402
from dbass_ai_agent.dbaas.config import dbaas_config_from_settings  # noqa: E402
from dbass_ai_agent.dbaas.constants import SERVICES_KIND  # noqa: E402
from dbass_ai_agent.dbaas.query import query_dbaas_data  # noqa: E402
from dbass_ai_agent.dbaas.sync import DbaasServiceSynchronizer, isoformat  # noqa: E402
from dbass_ai_agent.dbaas.workspace import DbaasWorkspace, read_json_file, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402
from dbass_ai_agent.sessions.models import ChatMessage, SessionMeta  # noqa: E402


class DbaasRealE2ETests(unittest.TestCase):
    def test_real_llm_answers_snapshot_success_and_unavailable_after_mock_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            port = _free_port()
            settings = _real_e2e_settings(root, port)
            config = dbaas_config_from_settings(settings)
            identity = Identity(user_id="payment-team-prod", role="user", user="payment-team-prod")

            with _mock_server(port):
                sync_meta = DbaasServiceSynchronizer(config).force_refresh_admin_services()
                self.assertEqual(sync_meta["status"], "fresh")

                query_result = query_dbaas_data(
                    config,
                    identity,
                    kind=SERVICES_KIND,
                    jq_filter='[.[] | select(.healthStatus != "HEALTHY")] | length',
                )
                self.assertEqual(query_result["status"], "success", query_result)
                expected_count = query_result["preview"]
                self.assertIsInstance(expected_count, int)

                runtime = DeepAgentRuntime(settings)
                try:
                    first_reply = runtime.generate_reply(
                        identity=identity,
                        session=_session_meta(identity, "thread_dbaas_real_e2e"),
                        user_message=_user_message(
                            "请使用 DBAAS 工具查询 services 中 healthStatus 不是 HEALTHY 的数量，"
                            "只回答数量和一句依据。"
                        ),
                    )
                finally:
                    _close_runtime(runtime)

            self.assertIn(str(expected_count), first_reply.content)

            _expire_snapshot(config)
            failed_meta = DbaasServiceSynchronizer(config).force_refresh_admin_services()
            self.assertEqual(failed_meta["status"], "error")
            workspace = DbaasWorkspace(config)
            self.assertFalse(workspace.data_path(SERVICES_KIND).exists())
            self.assertFalse(workspace.meta_path(SERVICES_KIND).exists())

            unavailable = query_dbaas_data(
                config,
                identity,
                kind=SERVICES_KIND,
                jq_filter='[.[] | select(.healthStatus != "HEALTHY")] | length',
            )
            self.assertEqual(unavailable["status"], "error")
            self.assertEqual(unavailable["error_type"], "snapshot_unavailable")

            runtime = DeepAgentRuntime(settings)
            try:
                second_reply = runtime.generate_reply(
                    identity=identity,
                    session=_session_meta(identity, "thread_dbaas_real_e2e_after_stop"),
                    user_message=_user_message(
                        "请重新查询 services 中 healthStatus 不是 HEALTHY 的数量。"
                        "如果当前没有可用快照，请明确说明无法获得准确数据，不要猜数量。"
                    ),
                )
            finally:
                _close_runtime(runtime)

            self.assertTrue(
                any(
                    marker in second_reply.content
                    for marker in ["无法获得准确数据", "没有可用", "快照", "后台同步", "拉取 DBAAS 数据失败"]
                ),
                second_reply.content,
            )


def _real_e2e_settings(root: Path, mock_port: int) -> Settings:
    if os.environ.get("DBAAS_REAL_E2E") != "1":
        raise unittest.SkipTest("真实 DBAAS E2E 未开启；设置 DBAAS_REAL_E2E=1 后才执行。")

    try:
        settings = Settings.from_file(DEFAULT_CONFIG_PATH)
    except ConfigError as exc:
        raise unittest.SkipTest(f"缺少真实 E2E 配置文件 {DEFAULT_CONFIG_PATH}: {exc}") from exc

    if not settings.model:
        raise unittest.SkipTest("真实 E2E 缺少 [model].model 配置。")
    if not settings.base_url:
        raise unittest.SkipTest("真实 E2E 缺少 [model].base_url 配置。")
    if not settings.api_key or settings.api_key == "replace-with-your-api-key":
        raise unittest.SkipTest("真实 E2E 缺少可用的 [model].api_key 配置。")

    return replace(
        settings,
        data_root=root / "users",
        runtime_root=root / "runtime",
        checkpoint_db=root / "runtime" / "checkpoints.sqlite",
        log_file=root / "logs" / "app.log",
        dbaas_server_base_url=f"http://127.0.0.1:{mock_port}",
        dbaas_workspace_dir=root / "runtime" / "dbaas_workspace",
        dbaas_sync_interval_seconds=1,
        dbaas_ttl_seconds=60,
        max_output_tokens=min(settings.max_output_tokens, 512),
    )


@contextmanager
def _mock_server(port: int):
    start_script = MOCK_SERVER_ROOT / "start.sh"
    if not start_script.exists():
        raise unittest.SkipTest(f"缺少 mock-server 启动脚本: {start_script}")

    env = {
        **os.environ,
        "HOST": "127.0.0.1",
        "PORT": str(port),
    }
    process = subprocess.Popen(
        [str(start_script)],
        cwd=MOCK_SERVER_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_services(port, process)
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _wait_for_services(port: int, process: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{port}/healthz"
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"mock-server exited early with code {process.returncode}")
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise TimeoutError(f"mock-server did not become ready on port {port}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _expire_snapshot(config) -> None:
    workspace = DbaasWorkspace(config)
    meta_path = workspace.meta_path(SERVICES_KIND)
    meta = read_json_file(meta_path)
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    meta["expires_at"] = isoformat(expired_at)
    write_meta_atomic(meta_path, meta)


def _session_meta(identity: Identity, thread_id: str) -> SessionMeta:
    now = datetime.now(UTC)
    return SessionMeta(
        session_id=f"sess_{thread_id}",
        user_id=identity.user_id,
        role=identity.role,
        user=identity.user,
        thread_id=thread_id,
        title="DBAAS real E2E",
        created_at=now,
        updated_at=now,
    )


def _user_message(content: str) -> ChatMessage:
    return ChatMessage(
        message_id=f"msg_{int(time.time() * 1000)}",
        role="user",
        content=content,
        created_at=datetime.now(UTC),
    )


def _close_runtime(runtime: DeepAgentRuntime) -> None:
    import asyncio

    asyncio.run(runtime.aclose())


if __name__ == "__main__":
    unittest.main()

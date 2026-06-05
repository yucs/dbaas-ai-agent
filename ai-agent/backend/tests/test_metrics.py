from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.config import APP_ROOT, Settings  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.dbaas.metric_catalog import describe_unit_metric_catalog  # noqa: E402
from dbass_ai_agent.dbaas.metric_cleanup import cleanup_metric_snapshots  # noqa: E402
from dbass_ai_agent.dbaas.metric_history import ensure_history_snapshot  # noqa: E402
from dbass_ai_agent.dbaas.metric_query import query_unit_latest_metric_data  # noqa: E402
from dbass_ai_agent.dbaas.metric_workspace import MetricWorkspace  # noqa: E402
from dbass_ai_agent.dbaas.snapshot_meta import isoformat, utcnow  # noqa: E402
from dbass_ai_agent.dbaas.tools import build_dbaas_tools  # noqa: E402
from dbass_ai_agent.dbaas.workspace import write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


class MetricCatalogTests(unittest.TestCase):
    def test_catalog_search_returns_compact_entries_without_score(self) -> None:
        result = describe_unit_metric_catalog("CPU", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        self.assertGreaterEqual(result["count"], 1)
        first = result["items"][0]
        self.assertEqual(first["metric_key"], "container.cpu.use")
        self.assertEqual(first["service_type"], "container")
        self.assertNotIn("service_types", first)
        self.assertNotIn("score", first)
        shapes = result["data_shapes"]
        self.assertEqual(shapes["latest"]["top_level"], "array")
        self.assertEqual(shapes["latest"]["jq_entry"], ".[]")
        self.assertFalse(shapes["latest"]["has_data_wrapper"])
        self.assertEqual(shapes["history"]["top_level"], "array")
        self.assertEqual(shapes["history"]["jq_entry"], ".[]")
        self.assertFalse(shapes["history"]["has_data_wrapper"])
        self.assertIn("ts", {field["name"] for field in shapes["history"]["item_fields"]})
        self.assertIn("value", {field["name"] for field in shapes["history"]["item_fields"]})

    def test_catalog_search_filters_by_single_service_type(self) -> None:
        result = describe_unit_metric_catalog("复制状态", service_type="mysql", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        self.assertEqual([item["metric_key"] for item in result["items"]], ["instance.mysql.replicationStatus"])
        self.assertEqual(result["items"][0]["service_type"], "mysql")


class MetricWorkspaceTests(unittest.TestCase):
    def test_latest_and_history_paths_are_scoped_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            workspace = MetricWorkspace(config)

            admin_latest = workspace.latest_paths(
                "container.cpu.use",
                Identity(user_id="admin", role="admin", user=None),
            )
            user_latest = workspace.latest_paths(
                "container.cpu.use",
                Identity(user_id="alice", role="user", user="payment/team"),
            )
            admin_history = workspace.history_paths(
                unit_name="mysql/primary 01",
                metric_key="container.cpu.use",
                start_ts=100,
                end_ts=200,
                identity=Identity(user_id="admin", role="admin", user=None),
            )
            history = workspace.history_paths(
                unit_name="mysql/primary 01",
                metric_key="container.cpu.use",
                start_ts=100,
                end_ts=200,
                identity=Identity(user_id="alice", role="user", user="payment/team"),
            )

            self.assertTrue(str(admin_latest.data_path).endswith("admin/metrics_latest/container.cpu.use.json"))
            self.assertEqual(admin_latest.key, "admin/metrics_latest/container.cpu.use")
            self.assertTrue(
                str(user_latest.data_path).endswith(
                    "users/payment_team/metrics_latest/container.cpu.use.json"
                )
            )
            self.assertEqual(user_latest.key, "users/payment_team/metrics_latest/container.cpu.use")
            self.assertIn(
                "admin/metrics_history/mysql_primary_01__container.cpu.use__100__200.json",
                str(admin_history.data_path),
            )
            self.assertIn(
                "users/payment_team/metrics_history/mysql_primary_01__container.cpu.use__100__200.json",
                str(history.data_path),
            )


class MetricQueryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("jq"), "jq is required for metric query tests")
    def test_query_latest_fetches_snapshot_with_identity_and_runs_jq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(
                _FakeResponse(
                    200,
                    [
                        {"service_name": "mysql-xf2", "unit_name": "mysql-0", "service_type": "mysql", "value": 72.5},
                        {"service_name": "redis-cache", "unit_name": "redis-0", "service_type": "redis", "value": 41},
                    ],
                )
            )

            with patch("dbass_ai_agent.dbaas.metric_sync.httpx.Client", return_value=fake_client):
                result = query_unit_latest_metric_data(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    metric_key="container.cpu.use",
                    jq_filter='[.[] | select(.service_type == "mysql" and .value > 60)]',
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "user")
            self.assertEqual(result["preview"][0]["unit_name"], "mysql-0")
            self.assertEqual(
                fake_client.last_headers,
                {
                    "Authorization": "Bearer user",
                    "X-DBAAS-Actor-User": "alice",
                    "X-DBAAS-Actor-Role": "user",
                },
            )
            self.assertEqual(fake_client.last_params, {"metric_key": "container.cpu.use"})

    def test_history_404_maps_to_resource_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(_FakeResponse(404, {"detail": "unit not found"}))
            end_ts = int(utcnow().timestamp()) - 60
            start_ts = end_ts - 60

            with patch("dbass_ai_agent.dbaas.metric_history.httpx.Client", return_value=fake_client):
                result = ensure_history_snapshot(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    unit_name="mysql-0",
                    metric_key="container.cpu.use",
                    start_ts=start_ts,
                    end_ts=end_ts,
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "resource_not_found")

    def test_history_rejects_invalid_time_range_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            result = ensure_history_snapshot(
                config,
                Identity(user_id="admin", role="admin", user=None),
                unit_name="mysql-0",
                metric_key="container.cpu.use",
                start_ts=100,
                end_ts=100,
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "history_time_range_invalid")


class MetricCleanupTests(unittest.TestCase):
    def test_cleanup_removes_expired_bad_missing_and_orphan_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            latest_dir = config.workspace_dir / "admin" / "metrics_latest"
            latest_dir.mkdir(parents=True)
            expired_data = latest_dir / "expired.json"
            expired_meta = latest_dir / "expired.meta.json"
            write_json_atomic(expired_data, [])
            write_meta_atomic(
                expired_meta,
                {
                    "status": "fresh",
                    "data_path": str(expired_data),
                    "expires_at": isoformat(utcnow() - timedelta(seconds=1)),
                },
            )
            bad_meta = latest_dir / "bad.meta.json"
            bad_meta.write_text("{bad", encoding="utf-8")
            missing_meta = latest_dir / "missing.meta.json"
            write_meta_atomic(
                missing_meta,
                {
                    "status": "fresh",
                    "data_path": str(latest_dir / "missing.json"),
                    "expires_at": isoformat(utcnow() + timedelta(seconds=60)),
                },
            )
            user_history_dir = config.workspace_dir / "users" / "payment_team" / "metrics_history"
            user_history_dir.mkdir(parents=True)
            orphan_data = user_history_dir / "orphan.json"
            write_json_atomic(orphan_data, [])

            result = cleanup_metric_snapshots(config)

            self.assertEqual(result.expired_pairs, 1)
            self.assertEqual(result.bad_meta, 1)
            self.assertEqual(result.missing_data_meta, 1)
            self.assertEqual(result.orphan_data, 1)
            self.assertFalse(expired_data.exists())
            self.assertFalse(expired_meta.exists())
            self.assertFalse(bad_meta.exists())
            self.assertFalse(missing_meta.exists())
            self.assertFalse(orphan_data.exists())


class MetricToolTests(unittest.TestCase):
    def test_build_dbaas_tools_includes_service_and_metric_tools(self) -> None:
        tool_names = {item.name for item in build_dbaas_tools(Settings(), role="user")}

        self.assertIn("query_dbaas_service_data_tool", tool_names)
        self.assertIn("describe_unit_metric_catalog_tool", tool_names)
        self.assertIn("query_unit_latest_metric_data_tool", tool_names)
        self.assertIn("query_unit_metric_history_tool", tool_names)
        self.assertIn("get_current_time_tool", tool_names)
        self.assertIn("precheck_service_resource_update_tool", tool_names)
        self.assertIn("precheck_service_storage_update_tool", tool_names)

    def test_build_dbaas_tools_adds_admin_only_tools_for_admin_role(self) -> None:
        admin_only_tool = SimpleNamespace(name="query_dbaas_host_tool")

        with patch(
            "dbass_ai_agent.dbaas.tools.build_admin_only_tools",
            return_value=[admin_only_tool],
        ):
            user_names = {item.name for item in build_dbaas_tools(Settings(), role="user")}
            admin_names = {item.name for item in build_dbaas_tools(Settings(), role="admin")}

        self.assertNotIn(admin_only_tool.name, user_names)
        self.assertIn(admin_only_tool.name, admin_names)


class _FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.last_url: str | None = None
        self.last_params: dict | None = None
        self.last_headers: dict | None = None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, *, params: dict, headers: dict):
        self.last_url = url
        self.last_params = params
        self.last_headers = headers
        return self.response


def _config(tmpdir: str) -> DbaasConfig:
    return DbaasConfig(
        server_base_url="http://127.0.0.1:9000",
        request_timeout_seconds=1,
        workspace_dir=Path(tmpdir) / "workspace",
        sync_interval_seconds=5,
        ttl_seconds=30,
        user_active_idle_timeout_seconds=300,
        user_snapshot_refresh_wait_seconds=3,
        jq_timeout_seconds=2,
        jq_max_preview_items=50,
        jq_max_output_bytes=1024 * 1024,
        metric_snapshot_ttl_seconds=30,
        metric_snapshot_cleanup_interval_seconds=600,
        metric_refresh_lock_timeout_seconds=10,
    )


if __name__ == "__main__":
    unittest.main()

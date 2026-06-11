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
from dbass_ai_agent.dbaas.metric_catalog import describe_unit_metric_catalog, get_metric_catalog_entry  # noqa: E402
from dbass_ai_agent.dbaas.metric_cleanup import cleanup_metric_snapshots  # noqa: E402
from dbass_ai_agent.dbaas.metric_history import ensure_history_snapshot  # noqa: E402
from dbass_ai_agent.dbaas.metric_query import query_unit_latest_metric_data  # noqa: E402
from dbass_ai_agent.dbaas.metric_workspace import MetricWorkspace  # noqa: E402
from dbass_ai_agent.dbaas.snapshot_meta import isoformat, utcnow  # noqa: E402
from dbass_ai_agent.dbaas.tools import build_dbaas_tools  # noqa: E402
from dbass_ai_agent.dbaas.workspace import write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


CPU_METRIC_KEY = "container.docker.cpu.avg_usage"
MYSQL_REPLICATION_STATUS_METRIC_KEY = "instance.upsql.replication.status"


class MetricCatalogTests(unittest.TestCase):
    def test_catalog_search_returns_compact_entries_without_score(self) -> None:
        result = describe_unit_metric_catalog("CPU", service_type="container", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mode"], "search")
        self.assertGreaterEqual(result["count"], 1)
        self.assertIn("catalog_semantics", result)
        first = result["items"][0]
        self.assertEqual(first["metric_key"], CPU_METRIC_KEY)
        self.assertEqual(first["service_type"], "container")
        self.assertNotIn("service_types", first)
        self.assertNotIn("score", first)
        self.assertNotIn("aliases", first)
        shapes = result["data_shapes"]
        self.assertEqual(shapes["latest"]["top_level"], "array")
        self.assertEqual(shapes["latest"]["jq_entry"], ".[]")
        self.assertFalse(shapes["latest"]["has_data_wrapper"])
        self.assertEqual(shapes["history"]["top_level"], "array")
        self.assertEqual(shapes["history"]["jq_entry"], ".[]")
        self.assertFalse(shapes["history"]["has_data_wrapper"])
        self.assertIn("ts", {field["name"] for field in shapes["history"]["item_fields"]})
        self.assertIn("value", {field["name"] for field in shapes["history"]["item_fields"]})
        semantics = result["catalog_semantics"]
        self.assertEqual(semantics["global_unit_metric_service_types"], ["container"])
        self.assertIn("container", semantics["service_type_semantics"])

    def test_catalog_search_requires_service_type(self) -> None:
        result = describe_unit_metric_catalog("CPU", app_root=APP_ROOT)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "metric_catalog_service_type_required")

    def test_catalog_lists_metrics_for_service_type_when_query_is_empty(self) -> None:
        result = describe_unit_metric_catalog(service_type="mysql", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mode"], "list")
        self.assertGreater(result["count"], 1)
        items_by_key = {item["metric_key"]: item for item in result["items"]}
        service_types = {item["service_type"] for item in result["items"]}
        self.assertIn(CPU_METRIC_KEY, items_by_key)
        self.assertIn("container", service_types)
        self.assertIn("mysql", service_types)
        self.assertTrue(result["truncated"])
        self.assertFalse(any("aliases" in item for item in result["items"]))

    def test_catalog_host_list_does_not_include_container_metrics(self) -> None:
        result = describe_unit_metric_catalog("", service_type="host", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["mode"], "list")
        self.assertGreater(result["count"], 1)
        service_types = {item["service_type"] for item in result["items"]}
        self.assertEqual(service_types, {"host"})

    def test_catalog_search_returns_container_metrics_for_service_resource_queries(self) -> None:
        result = describe_unit_metric_catalog("CPU占用率", service_type="mysql", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        items_by_key = {item["metric_key"]: item for item in result["items"]}
        self.assertIn(CPU_METRIC_KEY, items_by_key)
        self.assertEqual(items_by_key[CPU_METRIC_KEY]["service_type"], "container")

    def test_catalog_search_filters_by_single_service_type(self) -> None:
        result = describe_unit_metric_catalog("复制状态", service_type="mysql", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        items_by_key = {item["metric_key"]: item for item in result["items"]}
        self.assertIn(MYSQL_REPLICATION_STATUS_METRIC_KEY, items_by_key)
        item = items_by_key[MYSQL_REPLICATION_STATUS_METRIC_KEY]
        self.assertEqual(item["service_type"], "mysql")
        self.assertEqual(item["value_type"], "enum")
        self.assertEqual(item["enum_values"], ["passing", "warning", "critical", "unknown"])
        self.assertNotIn("normal_values", item)
        self.assertNotIn("abnormal_values", item)

    def test_catalog_search_matches_normalized_product_terms(self) -> None:
        result = describe_unit_metric_catalog("Redis 内存使用率", service_type="redis", app_root=APP_ROOT)

        self.assertEqual(result["status"], "success")
        self.assertIn(
            "instance.upredis.mem.used_percentage",
            {item["metric_key"] for item in result["items"]},
        )

    def test_catalog_search_matches_natural_language_aliases(self) -> None:
        cases = [
            ("CPU占用率", "container", "container.docker.cpu.avg_usage"),
            ("CPU占用率", "host", "host.linux.cpu.avg_usage"),
            ("memory", "container", "container.docker.mem.usage"),
            ("disk", "container", "container.docker.fs.datadir_usage"),
            ("storage usage", "mysql", "container.docker.fs.datadir_usage"),
            ("network receive", "mysql", "container.docker.network.receive_bytes_per_sec"),
            ("MySQL 是否正常", "mysql", "instance.upsql.running.status"),
            ("同步延迟", "mysql", "instance.upsql.replication.behind_master"),
            ("主从延迟", "mysql", "instance.upsql.replication.behind_master"),
            ("表空间容量", "mysql", "container.docker.fs.datadir_total"),
            ("日志目录容量", "mysql", "container.docker.fs.logdir_total"),
            ("磁盘占用率", "mysql", "container.docker.fs.datadir_usage"),
            ("根目录容量", "host", "host.linux.fs.rootdir_total"),
            ("每秒操作数", "redis", "instance.upredis.stat.ops_per_sec"),
            ("日志空间使用率", "container", "container.docker.fs.logdir_usage"),
        ]

        for query, service_type, metric_key in cases:
            with self.subTest(query=query, service_type=service_type):
                result = describe_unit_metric_catalog(query, service_type=service_type, app_root=APP_ROOT)

                self.assertEqual(result["status"], "success")
                self.assertIn(metric_key, {item["metric_key"] for item in result["items"]})

    def test_catalog_search_prioritizes_display_and_description_over_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app_root = Path(tmpdir)
            catalog_path = app_root / "config" / "dbaas_metric_catalog.json"
            write_json_atomic(
                catalog_path,
                [
                    {
                        "metric_key": "test.alias.exact",
                        "display_name": "普通别名命中",
                        "service_type": "mysql",
                        "value_type": "number",
                        "unit": None,
                        "aliases": ["延迟"],
                    },
                    {
                        "metric_key": "test.description.match",
                        "display_name": "描述命中",
                        "service_type": "mysql",
                        "value_type": "number",
                        "unit": None,
                        "aliases": ["普通指标"],
                        "description": "用于查询延迟相关监控。",
                    },
                    {
                        "metric_key": "test.display.match",
                        "display_name": "服务延迟",
                        "service_type": "mysql",
                        "value_type": "number",
                        "unit": None,
                        "aliases": ["普通指标"],
                    },
                    {
                        "metric_key": "test.alias.substring",
                        "display_name": "别名子串命中",
                        "service_type": "mysql",
                        "value_type": "number",
                        "unit": None,
                        "aliases": ["平均延迟指标"],
                    },
                ],
            )

            result = describe_unit_metric_catalog("延迟", service_type="mysql", app_root=app_root)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            [item["metric_key"] for item in result["items"]],
            [
                "test.display.match",
                "test.description.match",
                "test.alias.exact",
                "test.alias.substring",
            ],
        )

    def test_catalog_container_resource_units(self) -> None:
        expected_units = {
            "container.docker.network.receive_bytes_per_sec": "bytes/s",
            "container.docker.network.transmit_bytes_per_sec": "bytes/s",
            "container.docker.fs.datadir_total": "bytes",
            "container.docker.fs.datadir_usage": "%",
            "container.docker.fs.logdir_total": "bytes",
            "container.docker.fs.logdir_usage": "%",
        }

        for metric_key, unit in expected_units.items():
            with self.subTest(metric_key=metric_key):
                entry = get_metric_catalog_entry(metric_key, app_root=APP_ROOT)

                self.assertIsNotNone(entry)
                assert entry is not None
                self.assertEqual(entry.unit, unit)

    def test_catalog_aliases_are_kept_compact(self) -> None:
        for metric_key in [
            "container.docker.cpu.avg_usage",
            "container.docker.mem.usage",
            "container.docker.fs.datadir_usage",
            "container.docker.network.receive_bytes_per_sec",
            "instance.upsql.running.status",
        ]:
            with self.subTest(metric_key=metric_key):
                entry = get_metric_catalog_entry(metric_key, app_root=APP_ROOT)

                self.assertIsNotNone(entry)
                assert entry is not None
                self.assertLessEqual(len(entry.aliases), 8)

    def test_catalog_aliases_exclude_machine_tokens(self) -> None:
        machine_tokens = {
            "container",
            "容器",
            "docker",
            "host",
            "主机",
            "服务器",
            "linux",
            "status",
            "usage",
            "avg_usage",
            "cpu.avg_usage",
            "mysql",
            "MySQL",
            "redis",
            "Redis",
            "upsql",
            "upredis",
            "connection.current_number",
            "current_number",
        }

        for metric_key in [
            "container.docker.cpu.avg_usage",
            "host.linux.cpu.avg_usage",
            "instance.upsql.running.status",
            "instance.upredis.mem.used_percentage",
        ]:
            with self.subTest(metric_key=metric_key):
                entry = get_metric_catalog_entry(metric_key, app_root=APP_ROOT)

                self.assertIsNotNone(entry)
                assert entry is not None
                self.assertFalse(set(entry.aliases) & machine_tokens)


class MetricWorkspaceTests(unittest.TestCase):
    def test_latest_and_history_paths_are_scoped_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            workspace = MetricWorkspace(config)

            admin_latest = workspace.latest_paths(
                CPU_METRIC_KEY,
                Identity(user_id="admin", role="admin", user=None),
            )
            user_latest = workspace.latest_paths(
                CPU_METRIC_KEY,
                Identity(user_id="alice", role="user", user="payment/team"),
            )
            admin_history = workspace.history_paths(
                unit_name="mysql/primary 01",
                metric_key=CPU_METRIC_KEY,
                start_ts=100,
                end_ts=200,
                identity=Identity(user_id="admin", role="admin", user=None),
            )
            history = workspace.history_paths(
                unit_name="mysql/primary 01",
                metric_key=CPU_METRIC_KEY,
                start_ts=100,
                end_ts=200,
                identity=Identity(user_id="alice", role="user", user="payment/team"),
            )

            self.assertTrue(str(admin_latest.data_path).endswith(f"admin/metrics_latest/{CPU_METRIC_KEY}.json"))
            self.assertEqual(admin_latest.key, f"admin/metrics_latest/{CPU_METRIC_KEY}")
            self.assertTrue(
                str(user_latest.data_path).endswith(
                    f"users/payment_team/metrics_latest/{CPU_METRIC_KEY}.json"
                )
            )
            self.assertEqual(user_latest.key, f"users/payment_team/metrics_latest/{CPU_METRIC_KEY}")
            self.assertIn(
                f"admin/metrics_history/mysql_primary_01__{CPU_METRIC_KEY}__100__200.json",
                str(admin_history.data_path),
            )
            self.assertIn(
                f"users/payment_team/metrics_history/mysql_primary_01__{CPU_METRIC_KEY}__100__200.json",
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
                    metric_key=CPU_METRIC_KEY,
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
            self.assertEqual(fake_client.last_params, {"metric_key": CPU_METRIC_KEY})

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
                    metric_key=CPU_METRIC_KEY,
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
                metric_key=CPU_METRIC_KEY,
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
        service_sync_interval_seconds=5,
        service_snapshot_ttl_seconds=30,
        backup_snapshot_ttl_seconds=30,
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

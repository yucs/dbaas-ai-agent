from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from contextvars import copy_context
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.config import APP_ROOT, Settings  # noqa: E402
from dbass_ai_agent.dbaas.auth import dbaas_identity_headers, dbaas_system_headers  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.dbaas.constants import SERVICES_KIND  # noqa: E402
from dbass_ai_agent.dbaas.service_query import query_dbaas_service_data  # noqa: E402
from dbass_ai_agent.dbaas.schema import describe_schema, validate_payload  # noqa: E402
from dbass_ai_agent.dbaas.service_sync import DbaasServiceSynchronizer  # noqa: E402
from dbass_ai_agent.dbaas.snapshot_meta import isoformat, utcnow  # noqa: E402
from dbass_ai_agent.dbaas.tools import (  # noqa: E402
    build_dbaas_tools,
    dbaas_tool_identity,
)
from dbass_ai_agent.dbaas.workspace import DbaasWorkspace, write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


class DbaasSchemaTests(unittest.TestCase):
    def test_services_schema_accepts_service_list(self) -> None:
        validate_payload(SERVICES_KIND, [_service("mysql-xf2", "payment-team")], app_root=APP_ROOT)

    def test_describe_schema_returns_top_level_summary(self) -> None:
        summary = describe_schema(SERVICES_KIND, app_root=APP_ROOT)

        self.assertEqual(summary["schema_version"], "services.admin.v1")
        self.assertEqual(summary["top_level_type"], "array")
        self.assertTrue(any(field["name"] == "healthStatus" for field in summary["fields"]))

    def test_describe_schema_returns_user_projection_for_regular_user(self) -> None:
        summary = describe_schema(
            SERVICES_KIND,
            app_root=APP_ROOT,
            identity=Identity(user_id="alice", role="user", user="payment-team"),
        )

        self.assertEqual(summary["schema_version"], "services.user.v1")
        fields = {field["name"] for field in summary["fields"]}
        self.assertNotIn("siteId", fields)


class DbaasSyncTests(unittest.TestCase):
    def test_dbaas_identity_headers_include_actor_for_admin_user_and_system(self) -> None:
        self.assertEqual(
            dbaas_identity_headers(Identity(user_id="ops-admin", role="admin", user=None)),
            {
                "Authorization": "Bearer admin",
                "X-DBAAS-Actor-User": "ops-admin",
                "X-DBAAS-Actor-Role": "admin",
            },
        )
        self.assertEqual(
            dbaas_identity_headers(Identity(user_id="payment-team", role="user", user="payment-team")),
            {
                "Authorization": "Bearer user",
                "X-DBAAS-Actor-User": "payment-team",
                "X-DBAAS-Actor-Role": "user",
            },
        )
        self.assertEqual(
            dbaas_system_headers(),
            {
                "Authorization": "Bearer admin",
                "X-DBAAS-Actor-User": "dbaas-ai-agent",
                "X-DBAAS-Actor-Role": "system",
            },
        )

    def test_refresh_admin_services_writes_snapshot_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            synchronizer = DbaasServiceSynchronizer(config)

            with patch.object(
                DbaasServiceSynchronizer,
                "_fetch_services",
                return_value=[_service("mysql-xf2", "payment-team")],
            ):
                meta = synchronizer.refresh_admin_services()

            workspace = DbaasWorkspace(config)
            self.assertEqual(meta["status"], "fresh")
            self.assertEqual(meta["record_count"], 1)
            self.assertTrue(workspace.data_path(SERVICES_KIND).exists())
            self.assertTrue(workspace.meta_path(SERVICES_KIND).exists())

    @unittest.skipUnless(shutil.which("jq"), "jq is required for DBAAS query tests")
    def test_query_dbaas_service_data_reads_regular_user_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_user_snapshot(
                config,
                "payment-team",
                [
                    _service("mysql-a", "payment-team", health_status="HEALTHY"),
                    _service("mysql-b", "payment-team", health_status="UNHEALTHY"),
                ],
            )

            result = query_dbaas_service_data(
                config,
                Identity(user_id="alice", role="user", user="payment-team"),
                jq_filter='[.[] | select(.healthStatus != "HEALTHY") | {name, healthStatus}]',
                max_preview_items=10,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "user")
            self.assertEqual(result["preview"], [{"name": "mysql-b", "healthStatus": "UNHEALTHY"}])
            self.assertIsInstance(result["synced_at"], str)
            self.assertIsInstance(result["expires_at"], str)
            self.assertEqual(result["ttl_seconds"], config.ttl_seconds)

    def test_tool_identity_context_can_exit_from_different_context(self) -> None:
        manager = dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None))

        copy_context().run(manager.__enter__)
        copy_context().run(manager.__exit__, None, None, None)

    def test_expired_admin_snapshot_is_deleted_before_refresh_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            workspace = DbaasWorkspace(config)
            _write_expired_admin_snapshot(config, [_service("mysql-old", "payment-team")])

            with patch.object(
                DbaasServiceSynchronizer,
                "_fetch_services",
                side_effect=RuntimeError("dbaas unavailable"),
            ):
                meta = DbaasServiceSynchronizer(config).force_refresh_admin_services()

            self.assertEqual(meta["status"], "error")
            self.assertEqual(meta["error_type"], "snapshot_unavailable")
            self.assertFalse(workspace.data_path(SERVICES_KIND).exists())
            self.assertTrue(workspace.meta_path(SERVICES_KIND).exists())

    @unittest.skipUnless(shutil.which("jq"), "jq is required for DBAAS query tests")
    def test_fresh_snapshot_remains_queryable_when_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            workspace = DbaasWorkspace(config)
            _write_fresh_admin_snapshot(
                config,
                [
                    _service("mysql-a", "payment-team", health_status="HEALTHY"),
                    _service("mysql-b", "payment-team", health_status="UNHEALTHY"),
                ],
            )

            with patch.object(
                DbaasServiceSynchronizer,
                "_fetch_services",
                side_effect=RuntimeError("dbaas unavailable"),
            ):
                meta = DbaasServiceSynchronizer(config).force_refresh_admin_services()

            self.assertEqual(meta["status"], "fresh")
            self.assertEqual(meta["last_refresh_status"], "error")
            self.assertTrue(workspace.data_path(SERVICES_KIND).exists())
            self.assertTrue(workspace.meta_path(SERVICES_KIND).exists())

            result = query_dbaas_service_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter='[.[] | select(.healthStatus != "HEALTHY")] | length',
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["preview"], 1)

    def test_query_returns_error_when_snapshot_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)

            result = query_dbaas_service_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter=".[]",
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            self.assertIsNone(result["data_path"])

    def test_query_user_missing_snapshot_triggers_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)

            def _refresh(identity: Identity, *, timeout_seconds: int | None = None) -> dict:
                user = identity.user
                assert user is not None
                _write_fresh_user_snapshot(
                    config,
                    user,
                    [_service("mysql-a", user, health_status="UNHEALTHY")],
                )
                return {"status": "fresh"}

            with patch.object(DbaasServiceSynchronizer, "force_refresh_user_services", side_effect=_refresh) as refresh_user:
                result = query_dbaas_service_data(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    jq_filter='[.[] | {name, healthStatus}]',
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "user")
            refresh_user.assert_called_once()

    def test_query_refresh_for_admin_triggers_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)

            def _refresh() -> dict[str, object]:
                _write_fresh_admin_snapshot(
                    config,
                    [_service("mysql-a", "payment-team", health_status="UNHEALTHY")],
                )
                workspace = DbaasWorkspace(config)
                return {
                    "status": "fresh",
                    "last_refresh_status": "success",
                    "meta_path": str(workspace.meta_path(SERVICES_KIND)),
                }

            with patch.object(DbaasServiceSynchronizer, "force_refresh_admin_services", side_effect=_refresh) as refresh_admin:
                result = query_dbaas_service_data(
                    config,
                    Identity(user_id="admin", role="admin", user=None),
                    jq_filter='[.[] | {name, healthStatus}]',
                    refresh=True,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "admin")
            self.assertEqual(result["preview"], [{"name": "mysql-a", "healthStatus": "UNHEALTHY"}])
            refresh_admin.assert_called_once()

    def test_query_refresh_for_user_triggers_force_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)

            def _refresh(identity: Identity, *, timeout_seconds: int | None = None) -> dict[str, object]:
                user = identity.user
                assert user is not None
                _write_fresh_user_snapshot(
                    config,
                    user,
                    [_service("mysql-a", user, health_status="UNHEALTHY")],
                )
                workspace = DbaasWorkspace(config)
                paths = workspace.paths(SERVICES_KIND, scope="user", user=user)
                return {
                    "status": "fresh",
                    "scope": "user",
                    "user": user,
                    "last_refresh_status": "success",
                    "meta_path": str(paths.meta_path),
                }

            with patch.object(DbaasServiceSynchronizer, "force_refresh_user_services", side_effect=_refresh) as refresh_user:
                result = query_dbaas_service_data(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    jq_filter='[.[] | {name, healthStatus}]',
                    refresh=True,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "user")
            self.assertEqual(result["preview"], [{"name": "mysql-a", "healthStatus": "UNHEALTHY"}])
            refresh_user.assert_called_once()

    def test_query_user_stale_snapshot_does_not_trigger_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_expired_user_snapshot(config, "payment-team", [_service("mysql-old", "payment-team")])

            with patch.object(DbaasServiceSynchronizer, "force_refresh_user_services") as refresh_user:
                result = query_dbaas_service_data(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            refresh_user.assert_not_called()

    def test_query_user_error_snapshot_does_not_trigger_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_error_user_snapshot(config, "payment-team", "upstream failed")

            with patch.object(DbaasServiceSynchronizer, "force_refresh_user_services") as refresh_user:
                result = query_dbaas_service_data(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            refresh_user.assert_not_called()

    def test_query_does_not_trigger_sync_when_snapshot_is_expired(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_expired_admin_snapshot(config, [_service("mysql-old", "payment-team")])

            with patch.object(DbaasServiceSynchronizer, "_fetch_services") as fetch_services:
                result = query_dbaas_service_data(
                    config,
                    Identity(user_id="admin", role="admin", user=None),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            fetch_services.assert_not_called()

    def test_query_refresh_failure_does_not_fallback_to_old_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(
                config,
                [_service("mysql-old", "payment-team", health_status="HEALTHY")],
            )

            result = query_dbaas_service_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter='[.[] | {name, healthStatus}]',
                refresh=True,
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            self.assertIsNone(result["data_path"])
            self.assertIn("当前无法刷新 DBAAS 服务数据视图", result["message"])

    def test_query_error_message_is_suitable_for_user_answer_when_snapshot_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)

            result = query_dbaas_service_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter='[.[] | select(.healthStatus != "HEALTHY")] | length',
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            self.assertIn("当前没有可用的 DBAAS 服务数据视图", result["message"])
            self.assertIn("后台同步可能尚未完成", result["message"])


class DbaasPrecheckToolTests(unittest.TestCase):
    def test_resource_precheck_tool_posts_target_payload(self) -> None:
        tools = _tool_map()
        fake_client = _FakeDbaasHttpClient(
            _FakeDbaasResponse(
                200,
                _resource_precheck_response(),
            )
        )

        with (
            patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client),
            dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)),
        ):
            result = tools["precheck_service_resource_update_tool"].invoke(
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "mysql",
                    "target_cpu_cores": 8,
                    "target_memory_gb": 16,
                }
            )

        self.assertEqual(result["blocking_errors"], [])
        self.assertEqual(fake_client.last_method, "POST")
        self.assertTrue(fake_client.last_url.endswith("/api/v1/prechecks/service-resource-update"))
        self.assertEqual(
            fake_client.last_headers,
            {
                "Authorization": "Bearer admin",
                "X-DBAAS-Actor-User": "admin",
                "X-DBAAS-Actor-Role": "admin",
            },
        )
        self.assertEqual(
            fake_client.last_json,
            {
                "service_name": "mysql-xf2",
                "child_service_type": "mysql",
                "target_cpu_cores": 8,
                "target_memory_gb": 16,
            },
        )

    def test_storage_precheck_tool_returns_blocking_errors_from_dbaas(self) -> None:
        tools = _tool_map()
        fake_client = _FakeDbaasHttpClient(
            _FakeDbaasResponse(
                200,
                _storage_precheck_response(
                    blocking_errors=[
                        {
                            "code": "insufficient_capacity",
                            "message": "当前存储池资源不足，无法调整到目标值。",
                        }
                    ],
                ),
            )
        )

        with (
            patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client),
            dbaas_tool_identity(Identity(user_id="alice", role="user", user="payment-team-prod")),
        ):
            result = tools["precheck_service_storage_update_tool"].invoke(
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "mysql",
                    "target_data_volume_gb": 1_000_000,
                    "target_log_volume_gb": 1_000_000,
                }
            )

        self.assertEqual(result["blocking_errors"][0]["code"], "insufficient_capacity")
        self.assertTrue(fake_client.last_url.endswith("/api/v1/prechecks/service-storage-update"))
        self.assertEqual(
            fake_client.last_headers,
            {
                "Authorization": "Bearer user",
                "X-DBAAS-Actor-User": "alice",
                "X-DBAAS-Actor-Role": "user",
            },
        )
        self.assertEqual(
            fake_client.last_json,
            {
                "service_name": "mysql-xf2",
                "child_service_type": "mysql",
                "target_data_volume_gb": 1_000_000,
                "target_log_volume_gb": 1_000_000,
            },
        )

    def test_precheck_tool_returns_error_payload_when_dbaas_fails(self) -> None:
        tools = _tool_map()
        fake_client = _FakeDbaasHttpClient(
            _FakeDbaasResponse(502, {"detail": "service has no child service type"})
        )

        with (
            patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client),
            dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)),
        ):
            result = tools["precheck_service_resource_update_tool"].invoke(
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "redis",
                }
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "dbaas_request_failed")
        self.assertEqual(result["status_code"], 502)
        self.assertIn("service has no child service type", result["message"])

    def test_precheck_tool_handles_non_object_error_response(self) -> None:
        tools = _tool_map()
        fake_client = _FakeDbaasHttpClient(_FakeDbaasResponse(502, ["upstream unavailable"]))

        with (
            patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client),
            dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)),
        ):
            result = tools["precheck_service_resource_update_tool"].invoke(
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "mysql",
                }
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "dbaas_request_failed")
        self.assertEqual(result["status_code"], 502)
        self.assertIn("upstream unavailable", result["message"])

    def test_precheck_tool_returns_error_payload_when_required_field_missing(self) -> None:
        tools = _tool_map()
        fake_client = _FakeDbaasHttpClient(
            _FakeDbaasResponse(
                200,
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "mysql",
                    "blocking_errors": [],
                },
            )
        )

        with (
            patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client),
            dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)),
        ):
            result = tools["precheck_service_resource_update_tool"].invoke(
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "mysql",
                }
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "dbaas_invalid_response")
        self.assertIn("缺少必需字段", result["message"])

    def test_precheck_tool_returns_error_payload_when_blocking_errors_is_not_list(self) -> None:
        tools = _tool_map()
        payload = _storage_precheck_response()
        payload["blocking_errors"] = {"code": "insufficient_capacity"}
        fake_client = _FakeDbaasHttpClient(_FakeDbaasResponse(200, payload))

        with (
            patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client),
            dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)),
        ):
            result = tools["precheck_service_storage_update_tool"].invoke(
                {
                    "service_name": "mysql-xf2",
                    "child_service_type": "mysql",
                }
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "dbaas_invalid_response")
        self.assertIn("blocking_errors", result["message"])


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


def _resource_precheck_response(blocking_errors: list[dict] | None = None) -> dict:
    return {
        "service_name": "mysql-xf2",
        "child_service_type": "mysql",
        "current_spec": {
            "cpu_cores": 2,
            "memory_gb": 4,
        },
        "available_specs": [
            {
                "cpu_cores": 4,
                "memory_gb": 8,
                "label": "4C8G",
            }
        ],
        "runtime": {
            "unit_count": 1,
            "running_count": 1,
            "abnormal_units": [],
        },
        "metrics": {
            "time_window": "1d",
            "units": [
                {
                    "unit_name": "mysql-0",
                    "cpu": {
                        "latest": "82.5%",
                        "max": "96.8%",
                        "min": "21.3%",
                        "avg": "67.4%",
                    },
                    "memory": {
                        "latest": "71.2%",
                        "max": "84.6%",
                        "min": "48.9%",
                        "avg": "63.1%",
                    },
                }
            ],
            "missing_metric_units": [],
        },
        "blocking_errors": blocking_errors or [],
    }


def _storage_precheck_response(blocking_errors: list[dict] | None = None) -> dict:
    return {
        "service_name": "mysql-xf2",
        "child_service_type": "mysql",
        "current_storage": {
            "data_volume_gb": 500,
            "log_volume_gb": 100,
        },
        "runtime": {
            "unit_count": 1,
            "running_count": 1,
            "abnormal_units": [],
        },
        "metrics": {
            "units": [
                {
                    "unit_name": "mysql-0",
                    "data_usage": "78.5%",
                    "log_usage": "42.1%",
                }
            ],
            "missing_metric_units": [],
        },
        "blocking_errors": blocking_errors or [],
    }


def _service(name: str, user: str, *, health_status: str = "HEALTHY") -> dict:
    return {
        "name": name,
        "type": "mysql",
        "user": user,
        "subsystem": "payment",
        "environment": "prod",
        "siteId": "site-prod-sh-01",
        "siteName": "prod-sh-01",
        "region": "cn-east-1",
        "zone": "cn-east-1a",
        "architecture": "mysql",
        "sharding": False,
        "healthStatus": health_status,
        "network": {
            "vpcId": "vpc-prod-cn-east-1",
            "subnetId": "subnet-site-prod-sh-01-03",
            "cidr": "192.168.13.0/24",
            "gateway": "192.168.13.1",
        },
        "services": [],
        "backupStrategy": None,
    }


def _write_fresh_admin_snapshot(config: DbaasConfig, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    data_path = workspace.data_path(SERVICES_KIND)
    bytes_written = write_json_atomic(data_path, payload)
    now = utcnow()
    write_meta_atomic(
        workspace.meta_path(SERVICES_KIND),
        {
            "kind": SERVICES_KIND,
            "scope": "admin",
            "status": "fresh",
            "synced_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(seconds=config.ttl_seconds)),
            "ttl_seconds": config.ttl_seconds,
            "record_count": len(payload),
            "bytes": bytes_written,
            "data_path": str(data_path),
            "meta_path": str(workspace.meta_path(SERVICES_KIND)),
            "schema_version": "services.admin.v1",
            "schema_path": str(APP_ROOT / "config/schemas/services.admin.v1.schema.json"),
        },
    )


def _write_expired_admin_snapshot(config: DbaasConfig, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    data_path = workspace.data_path(SERVICES_KIND)
    bytes_written = write_json_atomic(data_path, payload)
    now = utcnow()
    write_meta_atomic(
        workspace.meta_path(SERVICES_KIND),
        {
            "kind": SERVICES_KIND,
            "scope": "admin",
            "status": "fresh",
            "synced_at": isoformat(now - timedelta(seconds=config.ttl_seconds * 2)),
            "expires_at": isoformat(now - timedelta(seconds=1)),
            "ttl_seconds": config.ttl_seconds,
            "record_count": len(payload),
            "bytes": bytes_written,
            "data_path": str(data_path),
            "meta_path": str(workspace.meta_path(SERVICES_KIND)),
            "schema_version": "services.admin.v1",
            "schema_path": str(APP_ROOT / "config/schemas/services.admin.v1.schema.json"),
        },
    )


def _write_fresh_user_snapshot(config: DbaasConfig, user: str, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    paths = workspace.paths(SERVICES_KIND, scope="user", user=user)
    bytes_written = write_json_atomic(paths.data_path, payload)
    now = utcnow()
    write_meta_atomic(
        paths.meta_path,
        {
            "kind": SERVICES_KIND,
            "scope": "user",
            "user": user,
            "status": "fresh",
            "synced_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(seconds=config.ttl_seconds)),
            "ttl_seconds": config.ttl_seconds,
            "record_count": len(payload),
            "bytes": bytes_written,
            "data_path": str(paths.data_path),
            "meta_path": str(paths.meta_path),
            "schema_version": "services.user.v1",
            "schema_path": str(APP_ROOT / "config/schemas/services.user.v1.schema.json"),
        },
    )


def _write_expired_user_snapshot(config: DbaasConfig, user: str, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    paths = workspace.paths(SERVICES_KIND, scope="user", user=user)
    bytes_written = write_json_atomic(paths.data_path, payload)
    now = utcnow()
    write_meta_atomic(
        paths.meta_path,
        {
            "kind": SERVICES_KIND,
            "scope": "user",
            "user": user,
            "status": "fresh",
            "synced_at": isoformat(now - timedelta(seconds=config.ttl_seconds * 2)),
            "expires_at": isoformat(now - timedelta(seconds=1)),
            "ttl_seconds": config.ttl_seconds,
            "record_count": len(payload),
            "bytes": bytes_written,
            "data_path": str(paths.data_path),
            "meta_path": str(paths.meta_path),
            "schema_version": "services.user.v1",
            "schema_path": str(APP_ROOT / "config/schemas/services.user.v1.schema.json"),
        },
    )


def _write_error_user_snapshot(config: DbaasConfig, user: str, message: str) -> None:
    workspace = DbaasWorkspace(config)
    paths = workspace.paths(SERVICES_KIND, scope="user", user=user)
    write_meta_atomic(
        paths.meta_path,
        {
            "kind": SERVICES_KIND,
            "scope": "user",
            "user": user,
            "status": "error",
            "error_type": "snapshot_unavailable",
            "synced_at": None,
            "expires_at": None,
            "ttl_seconds": config.ttl_seconds,
            "record_count": 0,
            "bytes": 0,
            "data_path": None,
            "meta_path": str(paths.meta_path),
            "schema_version": "services.user.v1",
            "schema_path": str(APP_ROOT / "config/schemas/services.user.v1.schema.json"),
            "last_refresh_status": "error",
            "last_error": message,
            "message": message,
        },
    )


def _tool_map() -> dict[str, object]:
    return {item.name: item for item in build_dbaas_tools(Settings(), role="user")}


class _FakeDbaasResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.reason_phrase = "error" if status_code >= 400 else "ok"
        self.text = str(payload)

    def json(self) -> object:
        return self._payload


class _FakeDbaasHttpClient:
    def __init__(self, response: _FakeDbaasResponse) -> None:
        self.response = response
        self.last_method: str | None = None
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_json: dict | None = None

    def __enter__(self) -> "_FakeDbaasHttpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict | None = None,
        params: dict | None = None,
    ) -> _FakeDbaasResponse:
        del params
        self.last_method = method
        self.last_url = url
        self.last_headers = headers
        self.last_json = json
        return self.response


def _read_json(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

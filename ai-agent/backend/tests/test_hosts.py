from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.config import APP_ROOT, Settings  # noqa: E402
from dbass_ai_agent.dbaas.background import DbaasBackgroundSync  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.dbaas.constants import HOSTS_KIND  # noqa: E402
from dbass_ai_agent.dbaas.host_query import query_dbaas_host_data  # noqa: E402
from dbass_ai_agent.dbaas.schema import DbaasSchemaError, describe_schema  # noqa: E402
from dbass_ai_agent.dbaas.tools import build_dbaas_tools, dbaas_tool_identity  # noqa: E402
from dbass_ai_agent.dbaas.workspace import DbaasWorkspace, read_json_file, write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


class HostSchemaTests(unittest.TestCase):
    def test_describe_schema_returns_full_hosts_schema_for_admin(self) -> None:
        summary = describe_schema(
            HOSTS_KIND,
            app_root=APP_ROOT,
            identity=Identity(user_id="admin", role="admin"),
        )

        self.assertEqual(summary["scope"], "admin")
        self.assertEqual(summary["schema_version"], "hosts.v1")
        self.assertEqual(summary["schema"]["type"], "array")
        properties = summary["schema"]["items"]["properties"]
        self.assertEqual(properties["status"]["type"], "string")
        self.assertIn("enabled", properties["status"]["enum"])
        self.assertIn("anyOf", properties["hdd"])
        self.assertIn("HostStorageDevice", summary["schema"]["$defs"])

    def test_describe_schema_rejects_hosts_for_regular_user(self) -> None:
        with self.assertRaises(DbaasSchemaError):
            describe_schema(
                HOSTS_KIND,
                app_root=APP_ROOT,
                identity=Identity(user_id="payment-team", role="user"),
            )


class HostQueryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("jq"), "jq is required for host query tests")
    def test_query_fetches_snapshot_with_admin_identity_and_runs_jq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(_FakeResponse(200, [_host("host-1"), _host("host-2", cpu_available=8)]))

            with patch("dbass_ai_agent.dbaas.host_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_host_data(
                    config,
                    Identity(user_id="ops-admin", role="admin"),
                    jq_filter='[.[] | select(.cpuAvailableCores >= 16) | {name, ip, cpuAvailableCores}]',
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["kind"], "hosts")
            self.assertEqual(result["scope"], "admin")
            self.assertEqual(result["preview"][0]["name"], "host-1")
            self.assertEqual(fake_client.last_url, "http://127.0.0.1:9000/hosts")
            self.assertEqual(
                fake_client.last_headers,
                {
                    "Authorization": "Bearer admin",
                    "X-DBAAS-Actor-User": "ops-admin",
                    "X-DBAAS-Actor-Role": "admin",
                },
            )

    def test_query_rejects_regular_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("dbass_ai_agent.dbaas.host_query.DbaasHostSynchronizer") as synchronizer:
                result = query_dbaas_host_data(
                    _config(tmpdir),
                    Identity(user_id="payment-team", role="user"),
                    jq_filter=".[]",
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "permission_denied")
        self.assertIsNone(result["data_path"])
        synchronizer.assert_not_called()

    @unittest.skipUnless(shutil.which("jq"), "jq is required for host query tests")
    def test_refresh_true_failure_does_not_use_existing_snapshot_as_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, [_host("host-old")])

            with patch(
                "dbass_ai_agent.dbaas.host_sync.httpx.Client",
                return_value=_FakeClient(_FakeResponse(500, {"detail": "boom"})),
            ):
                forced = query_dbaas_host_data(
                    config,
                    Identity(user_id="admin", role="admin"),
                    jq_filter='[.[] | .name]',
                    refresh=True,
                )

            self.assertEqual(forced["status"], "error")
            self.assertEqual(forced["error_type"], "refresh_failed")
            self.assertIsNone(forced["data_path"])

            workspace = DbaasWorkspace(config)
            self.assertTrue(workspace.data_path(HOSTS_KIND).exists())
            meta = read_json_file(workspace.meta_path(HOSTS_KIND))
            self.assertEqual(meta["status"], "fresh")
            self.assertEqual(meta["last_refresh_status"], "error")
            self.assertIn("DBAAS 主机接口返回异常状态", meta["last_error"])

            normal = query_dbaas_host_data(
                config,
                Identity(user_id="admin", role="admin"),
                jq_filter='[.[] | .name]',
                refresh=False,
            )
            self.assertEqual(normal["status"], "success")
            self.assertEqual(normal["preview"], ["host-old"])

    def test_invalid_host_payload_shape_returns_error_and_no_data_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(_FakeResponse(200, {"not": "array"}))

            with patch("dbass_ai_agent.dbaas.host_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_host_data(
                    config,
                    Identity(user_id="admin", role="admin"),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            self.assertIsNone(result["data_path"])
            workspace = DbaasWorkspace(config)
            self.assertFalse(workspace.data_path(HOSTS_KIND).exists())


class HostToolTests(unittest.TestCase):
    def test_build_dbaas_tools_registers_host_tool_only_for_admin(self) -> None:
        user_names = {item.name for item in build_dbaas_tools(Settings(), role="user")}
        admin_names = {item.name for item in build_dbaas_tools(Settings(), role="admin")}

        self.assertNotIn("query_dbaas_host_data_tool", user_names)
        self.assertIn("query_dbaas_host_data_tool", admin_names)

    def test_admin_schema_tool_describes_hosts_schema(self) -> None:
        tools = {item.name: item for item in build_dbaas_tools(Settings(), role="admin")}

        with dbaas_tool_identity(Identity(user_id="admin", role="admin")):
            result = tools["describe_dbaas_schema_tool"].invoke({"kind": "hosts"})

        self.assertEqual(result["kind"], "hosts")
        self.assertEqual(result["scope"], "admin")
        self.assertEqual(result["schema_version"], "hosts.v1")
        self.assertIn("healthStatus", result["schema"]["items"]["properties"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required for host query tests")
    def test_admin_host_tool_invokes_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(dbaas_workspace_dir=Path(tmpdir), dbaas_server_base_url="http://127.0.0.1:9000")
            tools = {item.name: item for item in build_dbaas_tools(settings, role="admin")}
            fake_client = _FakeClient(_FakeResponse(200, [_host("host-1")]))

            with patch("dbass_ai_agent.dbaas.host_sync.httpx.Client", return_value=fake_client):
                with dbaas_tool_identity(Identity(user_id="admin", role="admin")):
                    result = tools["query_dbaas_host_data_tool"].invoke({"jq_filter": "[.[] | .name]"})

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["preview"], ["host-1"])


class HostBackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_start_launches_host_sync_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                dbaas_workspace_dir=Path(tmpdir),
                dbaas_service_sync_interval_seconds=60,
                dbaas_host_sync_interval_seconds=60,
            )
            background = DbaasBackgroundSync(settings)
            background.synchronizer.force_refresh_admin_services = Mock(return_value={"status": "fresh"})  # type: ignore[method-assign]
            background.host_synchronizer.force_refresh_admin_hosts = Mock(return_value={"status": "fresh"})  # type: ignore[method-assign]

            background.start()
            await asyncio.sleep(0.05)
            await background.stop()

            background.host_synchronizer.force_refresh_admin_hosts.assert_called()


class _FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.last_headers: dict | None = None
        self.last_url: str | None = None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, *, headers: dict[str, str]):
        self.last_url = url
        self.last_headers = headers
        return self.response


def _config(tmpdir: str) -> DbaasConfig:
    return DbaasConfig(
        server_base_url="http://127.0.0.1:9000",
        request_timeout_seconds=5,
        workspace_dir=Path(tmpdir),
        service_sync_interval_seconds=5,
        service_snapshot_ttl_seconds=30,
        backup_snapshot_ttl_seconds=30,
        user_active_idle_timeout_seconds=300,
        user_snapshot_refresh_wait_seconds=3,
        jq_timeout_seconds=3,
        jq_max_preview_items=50,
        jq_max_output_bytes=16384,
        metric_snapshot_ttl_seconds=30,
        metric_snapshot_cleanup_interval_seconds=600,
        metric_refresh_lock_timeout_seconds=10,
        host_sync_interval_seconds=60,
        host_snapshot_ttl_seconds=120,
        host_refresh_lock_timeout_seconds=10,
    )


def _write_fresh_admin_snapshot(config: DbaasConfig, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    data_path = workspace.data_path(HOSTS_KIND)
    meta_path = workspace.meta_path(HOSTS_KIND)
    write_json_atomic(data_path, payload)
    write_meta_atomic(
        meta_path,
        {
            "kind": HOSTS_KIND,
            "scope": "admin",
            "user": None,
            "version": 1,
            "data_path": str(data_path),
            "meta_path": str(meta_path),
            "status": "fresh",
            "synced_at": "2026-06-01T10:00:00Z",
            "expires_at": "2099-06-01T10:02:00Z",
            "ttl_seconds": config.host_snapshot_ttl_seconds,
            "record_count": len(payload),
            "bytes": 1,
            "source": "dbaas-server",
            "source_endpoint": "/hosts",
            "schema_version": "hosts.v1",
            "schema_path": str((APP_ROOT / "config/schemas/hosts.v1.schema.json").resolve()),
            "last_refresh_status": "success",
            "last_error": None,
        },
    )


def _host(name: str, *, cpu_available: int = 20) -> dict:
    return {
        "id": "4212111182",
        "name": name,
        "ip": "192.18.11.11",
        "sshPort": 22,
        "siteId": "585430486",
        "siteName": "上海PIT站",
        "clusterId": "1026800163",
        "clusterName": "上海PIT站 Cluster 01",
        "clusterEnabled": True,
        "areaId": "1664968891",
        "areaName": "核心区",
        "room": "CN-EAST-1-ROOM-01",
        "seat": "CN-EAST-1-01-01",
        "networkPartition": "ha-a",
        "status": "enabled",
        "healthStatus": "HEALTHY",
        "cpuArchitecture": "amd64",
        "cpuArchitectureName": "X86",
        "cpuCapacityCores": 48,
        "cpuAllocatedCores": 48 - cpu_available,
        "cpuAvailableCores": cpu_available,
        "cpuAllocationPercent": 58.3,
        "memoryCapacityGB": 240,
        "memoryAllocatedGB": 104,
        "memoryAvailableGB": 136,
        "memoryAllocationPercent": 43.3,
        "hdd": {
            "device": "/dev/sdb",
            "capacityGB": 8192,
            "usedGB": 4170.1,
            "availableGB": 4021.9,
            "usagePercent": 50.9,
        },
        "ssd": None,
        "sanName": None,
        "maxUnitCount": 80,
        "maxUsagePercent": 80,
        "unitCount": 6,
        "createdAt": "2026-05-24 10:23:00",
        "creator": "03001007",
        "creatorName": "陈思远",
    }


if __name__ == "__main__":
    unittest.main()

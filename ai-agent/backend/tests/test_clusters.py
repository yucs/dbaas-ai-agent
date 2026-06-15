from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.config import APP_ROOT, Settings  # noqa: E402
from dbass_ai_agent.dbaas.cluster_query import query_dbaas_cluster_data  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.dbaas.constants import CLUSTERS_KIND  # noqa: E402
from dbass_ai_agent.dbaas.schema import DbaasSchemaError, describe_schema  # noqa: E402
from dbass_ai_agent.dbaas.tools import build_dbaas_tools, dbaas_tool_identity  # noqa: E402
from dbass_ai_agent.dbaas.workspace import DbaasWorkspace, read_json_file, write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


class ClusterSchemaTests(unittest.TestCase):
    def test_describe_schema_returns_full_clusters_schema_for_admin(self) -> None:
        summary = describe_schema(
            CLUSTERS_KIND,
            app_root=APP_ROOT,
            identity=Identity(user_id="admin", role="admin"),
        )

        self.assertEqual(summary["scope"], "admin")
        self.assertEqual(summary["schema_version"], "clusters.v1")
        self.assertEqual(summary["schema"]["type"], "array")
        self.assertFalse(summary["schema"]["items"]["additionalProperties"])
        properties = summary["schema"]["items"]["properties"]
        self.assertEqual(properties["enabled"]["type"], "boolean")
        self.assertEqual(properties["supportedSoftwareTypes"]["type"], "array")
        self.assertEqual(properties["supportedNetworkNames"]["type"], "array")

    def test_describe_schema_rejects_clusters_for_regular_user(self) -> None:
        with self.assertRaises(DbaasSchemaError):
            describe_schema(
                CLUSTERS_KIND,
                app_root=APP_ROOT,
                identity=Identity(user_id="alice", role="user"),
            )


class ClusterQueryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("jq"), "jq is required for cluster query tests")
    def test_query_fetches_snapshot_with_admin_identity_and_runs_jq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(
                _FakeResponse(
                    200,
                    [
                        _cluster("cluster-mysql", supported_software_types=["mysql", "redis"]),
                        _cluster("cluster-kafka", supported_software_types=["upkafka"], network_names=["LEAF-10.26.18"]),
                    ],
                )
            )

            with patch("dbass_ai_agent.dbaas.cluster_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_cluster_data(
                    config,
                    Identity(user_id="ops-admin", role="admin"),
                    jq_filter='[.[] | select(.supportedSoftwareTypes | index("mysql")) | {name, supportedNetworkNames}]',
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["kind"], "clusters")
            self.assertEqual(result["scope"], "admin")
            self.assertEqual(result["preview"][0]["name"], "cluster-mysql")
            self.assertEqual(fake_client.last_url, "http://127.0.0.1:9000/clusters")
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
            with patch("dbass_ai_agent.dbaas.cluster_query.DbaasClusterSynchronizer") as synchronizer:
                result = query_dbaas_cluster_data(
                    _config(tmpdir),
                    Identity(user_id="alice", role="user"),
                    jq_filter=".[]",
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "permission_denied")
        self.assertIsNone(result["data_path"])
        synchronizer.assert_not_called()

    @unittest.skipUnless(shutil.which("jq"), "jq is required for cluster query tests")
    def test_refresh_true_failure_does_not_use_existing_snapshot_as_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, [_cluster("cluster-old")])

            with patch(
                "dbass_ai_agent.dbaas.cluster_sync.httpx.Client",
                return_value=_FakeClient(_FakeResponse(500, {"detail": "boom"})),
            ):
                forced = query_dbaas_cluster_data(
                    config,
                    Identity(user_id="admin", role="admin"),
                    jq_filter='[.[] | .name]',
                    refresh=True,
                )

            self.assertEqual(forced["status"], "error")
            self.assertEqual(forced["error_type"], "refresh_failed")
            self.assertIsNone(forced["data_path"])

            workspace = DbaasWorkspace(config)
            self.assertTrue(workspace.data_path(CLUSTERS_KIND).exists())
            meta = read_json_file(workspace.meta_path(CLUSTERS_KIND))
            self.assertEqual(meta["status"], "fresh")
            self.assertEqual(meta["last_refresh_status"], "error")
            self.assertIn("DBAAS 集群接口返回异常状态", meta["last_error"])

            normal = query_dbaas_cluster_data(
                config,
                Identity(user_id="admin", role="admin"),
                jq_filter='[.[] | .name]',
                refresh=False,
            )
            self.assertEqual(normal["status"], "success")
            self.assertEqual(normal["preview"], ["cluster-old"])

    def test_invalid_cluster_payload_shape_returns_error_and_no_data_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(_FakeResponse(200, {"not": "array"}))

            with patch("dbass_ai_agent.dbaas.cluster_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_cluster_data(
                    config,
                    Identity(user_id="admin", role="admin"),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            self.assertIsNone(result["data_path"])
            workspace = DbaasWorkspace(config)
            self.assertFalse(workspace.data_path(CLUSTERS_KIND).exists())

    def test_invalid_cluster_record_schema_returns_error_and_no_data_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            invalid_cluster = {
                **_cluster("cluster-invalid"),
                "id": 3101,
            }
            fake_client = _FakeClient(_FakeResponse(200, [invalid_cluster]))

            with patch("dbass_ai_agent.dbaas.cluster_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_cluster_data(
                    config,
                    Identity(user_id="admin", role="admin"),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            self.assertIn("字段 id 不是字符串", result["message"])
            workspace = DbaasWorkspace(config)
            self.assertFalse(workspace.data_path(CLUSTERS_KIND).exists())


class ClusterToolTests(unittest.TestCase):
    def test_build_dbaas_tools_registers_cluster_tool_only_for_admin(self) -> None:
        user_names = {item.name for item in build_dbaas_tools(Settings(), role="user")}
        admin_names = {item.name for item in build_dbaas_tools(Settings(), role="admin")}

        self.assertNotIn("query_dbaas_cluster_data_tool", user_names)
        self.assertIn("query_dbaas_cluster_data_tool", admin_names)

    def test_admin_schema_tool_describes_clusters_schema(self) -> None:
        tools = {item.name: item for item in build_dbaas_tools(Settings(), role="admin")}

        with dbaas_tool_identity(Identity(user_id="admin", role="admin")):
            result = tools["describe_dbaas_schema_tool"].invoke({"kind": "clusters"})

        self.assertEqual(result["kind"], "clusters")
        self.assertEqual(result["scope"], "admin")
        self.assertEqual(result["schema_version"], "clusters.v1")
        self.assertIn("supportedSoftwareTypes", result["schema"]["items"]["properties"])

    def test_schema_tool_description_mentions_clusters(self) -> None:
        tools = {item.name: item for item in build_dbaas_tools(Settings(), role="admin")}

        description = tools["describe_dbaas_schema_tool"].description

        self.assertIn("kind=clusters", description)

    @unittest.skipUnless(shutil.which("jq"), "jq is required for cluster query tests")
    def test_admin_cluster_tool_invokes_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(dbaas_workspace_dir=Path(tmpdir), dbaas_server_base_url="http://127.0.0.1:9000")
            tools = {item.name: item for item in build_dbaas_tools(settings, role="admin")}
            fake_client = _FakeClient(_FakeResponse(200, [_cluster("cluster-1")]))

            with patch("dbass_ai_agent.dbaas.cluster_sync.httpx.Client", return_value=fake_client):
                with dbaas_tool_identity(Identity(user_id="admin", role="admin")):
                    result = tools["query_dbaas_cluster_data_tool"].invoke({"jq_filter": "[.[] | .name]"})

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["preview"], ["cluster-1"])


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
        cluster_snapshot_ttl_seconds=120,
        cluster_refresh_lock_timeout_seconds=10,
    )


def _write_fresh_admin_snapshot(config: DbaasConfig, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    data_path = workspace.data_path(CLUSTERS_KIND)
    meta_path = workspace.meta_path(CLUSTERS_KIND)
    write_json_atomic(data_path, payload)
    write_meta_atomic(
        meta_path,
        {
            "kind": CLUSTERS_KIND,
            "scope": "admin",
            "user": None,
            "version": 1,
            "data_path": str(data_path),
            "meta_path": str(meta_path),
            "status": "fresh",
            "synced_at": "2026-06-01T10:00:00Z",
            "expires_at": "2099-06-01T10:02:00Z",
            "ttl_seconds": config.cluster_snapshot_ttl_seconds,
            "record_count": len(payload),
            "bytes": 1,
            "source": "dbaas-server",
            "source_endpoint": "/clusters",
            "schema_version": "clusters.v1",
            "schema_path": str((APP_ROOT / "config/schemas/clusters.v1.schema.json").resolve()),
            "last_refresh_status": "success",
            "last_error": None,
        },
    )


def _cluster(
    name: str,
    *,
    supported_software_types: list[str] | None = None,
    network_names: list[str] | None = None,
) -> dict:
    return {
        "id": "3101",
        "name": name,
        "siteId": "12",
        "siteName": "南京一区",
        "areaId": "8",
        "areaName": "核心区",
        "supportedCpuArchitectures": ["amd64"],
        "supportedCpuArchitectureNames": ["X86"],
        "supportedSoftwareTypes": supported_software_types or ["mysql", "redis", "mongodb"],
        "supportedNetworkNames": network_names or ["LEAF-10.24.16", "LEAF-10.24.17"],
        "haNetworkTag": name,
        "enabled": True,
        "description": "核心数据库集群",
        "createdAt": "2026-05-18 10:23:00",
        "createdBy": "ops_admin",
        "createdByName": "运维管理员",
        "updatedAt": "2026-05-20 15:42:11",
        "updatedBy": "ops_admin",
        "updatedByName": "运维管理员",
    }


if __name__ == "__main__":
    unittest.main()

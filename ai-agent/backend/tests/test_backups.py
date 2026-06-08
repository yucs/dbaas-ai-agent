from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.config import APP_ROOT, Settings  # noqa: E402
from dbass_ai_agent.dbaas.backup_query import query_dbaas_backup_data  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.dbaas.constants import BACKUPS_KIND  # noqa: E402
from dbass_ai_agent.dbaas.schema import describe_schema  # noqa: E402
from dbass_ai_agent.dbaas.tools import build_dbaas_tools, dbaas_tool_identity  # noqa: E402
from dbass_ai_agent.dbaas.workspace import DbaasWorkspace, write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


class BackupSchemaTests(unittest.TestCase):
    def test_describe_schema_returns_backups_field_summary(self) -> None:
        summary = describe_schema(BACKUPS_KIND, app_root=APP_ROOT)

        self.assertEqual(summary["schema_version"], "backups.v1")
        self.assertEqual(summary["top_level_type"], "array")
        fields = {field["name"]: field for field in summary["fields"]}
        self.assertEqual(fields["task_status"]["type"], "string")
        self.assertFalse(fields["task_status"]["nullable"])
        self.assertIn("succeeded", fields["task_status"]["enum_values"])
        self.assertTrue(fields["finished_at"]["nullable"])


class BackupQueryTests(unittest.TestCase):
    def test_describe_backup_capability_tool_calls_dbaas_with_target_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                dbaas_server_base_url="http://127.0.0.1:9000",
                dbaas_workspace_dir=Path(tmpdir) / "workspace",
            )
            identity = Identity(user_id="admin", role="admin", user=None)
            fake_client = _FakeClient(
                _FakeResponse(
                    200,
                    {
                        "supported": True,
                        "serviceType": "mysql",
                        "fields": [
                            {"name": "backupType", "required": True},
                            {"name": "retentionDays", "required": True},
                        ],
                    },
                )
            )

            with patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client):
                tools = {tool.name: tool for tool in build_dbaas_tools(settings, role="admin")}
                with dbaas_tool_identity(identity):
                    result = tools["describe_service_backup_capability_tool"].invoke(
                        {
                            "service_name": "mysql-xf2",
                            "unit_name": "mysql-primary-01",
                        }
                    )

            self.assertTrue(result["supported"])
            self.assertEqual(fake_client.last_method, "GET")
            self.assertEqual(fake_client.last_url, "http://127.0.0.1:9000/backup-task-capabilities")
            self.assertEqual(
                fake_client.last_params,
                {
                    "serviceName": "mysql-xf2",
                    "unitName": "mysql-primary-01",
                },
            )
            self.assertEqual(fake_client.last_headers["X-DBAAS-Actor-Role"], "admin")

    def test_describe_backup_capability_tool_requires_target(self) -> None:
        tools = {tool.name: tool for tool in build_dbaas_tools(Settings(), role="admin")}

        with dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)):
            result = tools["describe_service_backup_capability_tool"].invoke({})

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "missing_target")

    def test_describe_image_upgrade_capability_tool_calls_dbaas_with_target_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                dbaas_server_base_url="http://127.0.0.1:9000",
                dbaas_workspace_dir=Path(tmpdir) / "workspace",
            )
            identity = Identity(user_id="admin", role="admin", user=None)
            fake_client = _FakeClient(
                _FakeResponse(
                    200,
                    {
                        "supported": True,
                        "availableTargets": [
                            {
                                "image": "mysql:8.0.37",
                                "version": "8.0.37",
                            }
                        ],
                    },
                )
            )

            with patch("dbass_ai_agent.dbaas.write_client.httpx.Client", return_value=fake_client):
                tools = {tool.name: tool for tool in build_dbaas_tools(settings, role="admin")}
                with dbaas_tool_identity(identity):
                    result = tools["describe_service_image_upgrade_capability_tool"].invoke(
                        {
                            "service_name": "mysql-xf2",
                            "child_service_type": "mysql",
                        }
                    )

            self.assertTrue(result["supported"])
            self.assertEqual(fake_client.last_method, "GET")
            self.assertEqual(fake_client.last_url, "http://127.0.0.1:9000/image-upgrade-capabilities")
            self.assertEqual(
                fake_client.last_params,
                {
                    "serviceName": "mysql-xf2",
                    "childServiceType": "mysql",
                },
            )
            self.assertEqual(fake_client.last_headers["X-DBAAS-Actor-Role"], "admin")

    def test_describe_image_upgrade_capability_tool_requires_target(self) -> None:
        tools = {tool.name: tool for tool in build_dbaas_tools(Settings(), role="admin")}

        with dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None)):
            result = tools["describe_service_image_upgrade_capability_tool"].invoke(
                {
                    "service_name": "",
                    "child_service_type": "mysql",
                }
            )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "missing_target")

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_fetches_snapshot_with_identity_and_runs_jq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(
                _FakeResponse(
                    200,
                    [
                        _backup("backup-1", "mysql-xf2", unit_name="mysql-primary-01"),
                        _backup("backup-2", "redis-cache", service_type="redis", child_service_type="redis", unit_name="redis-primary-01"),
                    ],
                )
            )

            with patch("dbass_ai_agent.dbaas.backup_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_backup_data(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                    jq_filter='[.[] | select(.service_name == "mysql-xf2")]',
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "user")
            self.assertEqual(result["preview"][0]["backup_id"], "backup-1")
            self.assertEqual(
                fake_client.last_headers,
                {
                    "Authorization": "Bearer user",
                    "X-DBAAS-Actor-User": "alice",
                    "X-DBAAS-Actor-Role": "user",
                },
            )

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_refresh_true_failure_does_not_use_existing_snapshot_as_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, [_backup("backup-1", "mysql-xf2")])

            with patch(
                "dbass_ai_agent.dbaas.backup_sync.httpx.Client",
                return_value=_FakeClient(_FakeResponse(500, {"detail": "boom"})),
            ):
                forced = query_dbaas_backup_data(
                    config,
                    Identity(user_id="admin", role="admin", user=None),
                    jq_filter='[.[] | .backup_id]',
                    refresh=True,
                )

            self.assertEqual(forced["status"], "error")
            self.assertEqual(forced["error_type"], "dbaas_request_failed")

            normal = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter='[.[] | .backup_id]',
                refresh=False,
            )
            self.assertEqual(normal["status"], "success")
            self.assertEqual(normal["preview"], ["backup-1"])

    def test_invalid_backup_payload_shape_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            fake_client = _FakeClient(_FakeResponse(200, {"not": "array"}))

            with patch("dbass_ai_agent.dbaas.backup_sync.httpx.Client", return_value=fake_client):
                result = query_dbaas_backup_data(
                    config,
                    Identity(user_id="admin", role="admin", user=None),
                    jq_filter=".[]",
                )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "snapshot_unavailable")
            workspace = DbaasWorkspace(config)
            self.assertFalse(workspace.data_path(BACKUPS_KIND).exists())

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_lists_service_backups_in_past_week(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, _mysql_xf2_backup_records())

            result = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter=(
                    '[.[] | select(.service_name == "mysql-xf2"'
                    ' and .started_at >= "2026-05-26 00:00:00"'
                    ' and .started_at < "2026-06-02 00:00:00")'
                    ' | {backup_id, started_at, task_status}]'
                    ' | sort_by(.started_at)'
                ),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(
                [item["backup_id"] for item in result["preview"]],
                [
                    "backup-mysql-xf2-20260527-primary",
                    "backup-mysql-xf2-20260529-primary-failed",
                    "backup-mysql-xf2-20260530-replica-expired",
                    "backup-mysql-xf2-20260531-primary",
                    "backup-mysql-xf2-20260601-replica-failed",
                    "backup-mysql-xf2-20260601-replica",
                ],
            )

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_counts_successful_backups_in_past_three_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, _mysql_xf2_backup_records())

            result = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter=(
                    '[.[] | select(.service_name == "mysql-xf2"'
                    ' and .started_at >= "2026-05-29 00:00:00"'
                    ' and .started_at < "2026-06-02 00:00:00"'
                    ' and .task_status == "succeeded")]'
                    ' | length'
                ),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["preview"], 3)

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_finds_expired_but_not_deleted_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, _mysql_xf2_backup_records())

            result = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter=(
                    '[.[] | select(.service_name == "mysql-xf2"'
                    ' and .expires_at != null'
                    ' and .expires_at < "2026-06-01 00:00:00")'
                    ' | {backup_id, expires_at, remark}]'
                    ' | sort_by(.expires_at)'
                ),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(
                result["preview"],
                [
                    {
                        "backup_id": "backup-mysql-xf2-20260530-replica-expired",
                        "expires_at": "2026-05-31 03:05:00",
                        "remark": "已过期但未删除",
                    }
                ],
            )

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_reports_failed_backup_count_and_last_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, _mysql_xf2_backup_records())

            result = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter=(
                    '[.[] | select(.service_name == "mysql-xf2"'
                    ' and .started_at >= "2026-05-26 00:00:00"'
                    ' and .started_at < "2026-06-02 00:00:00"'
                    ' and .task_status == "failed")]'
                    ' | {'
                    'failed_count: length,'
                    'last_failure: (sort_by(.started_at) | last | {backup_id, started_at, task_error})'
                    ' }'
                ),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(
                result["preview"][0],
                {
                    "failed_count": 2,
                    "last_failure": {
                        "backup_id": "backup-mysql-xf2-20260601-replica-failed",
                        "started_at": "2026-06-01 01:00:00",
                        "task_error": "S3 upload failed",
                    },
                },
            )

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_groups_latest_success_backup_by_child_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, _mysql_xf2_backup_records())

            result = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter=(
                    '[.[] | select(.service_name == "mysql-xf2"'
                    ' and .task_status == "succeeded")]'
                    ' | group_by(.child_service_name)'
                    ' | map(sort_by(.started_at) | last | {child_service_name, backup_id, started_at})'
                    ' | sort_by(.child_service_name)'
                ),
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(
                result["preview"],
                [
                    {
                        "child_service_name": "mysql-primary",
                        "backup_id": "backup-mysql-xf2-20260531-primary",
                        "started_at": "2026-05-31 21:30:00",
                    },
                    {
                        "child_service_name": "mysql-replica",
                        "backup_id": "backup-mysql-xf2-20260601-replica",
                        "started_at": "2026-06-01 08:15:00",
                    },
                ],
            )

    @unittest.skipUnless(shutil.which("jq"), "jq is required for backup query tests")
    def test_query_large_backup_result_is_preview_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(config, _many_backup_records())

            result = query_dbaas_backup_data(
                config,
                Identity(user_id="admin", role="admin", user=None),
                jq_filter='[.[] | select(.service_name == "mysql-xf2") | {backup_id, started_at}] | sort_by(.started_at)',
                max_preview_items=5,
            )

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["truncated"])
            self.assertEqual(result["preview_count"], 5)
            self.assertEqual(result["preview"][0]["backup_id"], "backup-mysql-xf2-bulk-000")
            self.assertEqual(result["preview"][-1]["backup_id"], "backup-mysql-xf2-bulk-004")


class BackupToolTests(unittest.TestCase):
    def test_build_dbaas_tools_includes_backup_query_tool(self) -> None:
        tool_names = {item.name for item in build_dbaas_tools(Settings(), role="user")}

        self.assertIn("query_dbaas_backup_data_tool", tool_names)

    def test_build_runtime_tools_exposes_backup_query_tool(self) -> None:
        admin_only_tool = SimpleNamespace(name="query_dbaas_host_tool")

        with patch(
            "dbass_ai_agent.dbaas.tools.build_admin_only_tools",
            return_value=[admin_only_tool],
        ):
            admin_names = {item.name for item in build_dbaas_tools(Settings(), role="admin")}

        self.assertIn("query_dbaas_backup_data_tool", admin_names)


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
        self.last_method: str | None = None
        self.last_url: str | None = None
        self.last_params: dict | None = None
        self.last_json: dict | None = None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, *, headers: dict[str, str]):
        self.last_headers = headers
        return self.response

    def request(self, method: str, url: str, *, headers: dict[str, str], json=None, params=None):
        self.last_method = method
        self.last_url = url
        self.last_headers = headers
        self.last_json = json
        self.last_params = params
        return self.response


def _backup(
    backup_id: str,
    service_name: str,
    *,
    service_type: str = "mysql",
    child_service_name: str = "mysql-primary",
    child_service_type: str = "mysql",
    unit_name: str = "mysql-primary-01",
    backup_type: str = "full",
    backup_path: str | None = None,
    size_bytes: int = 1024,
    storage_type: str | None = "NAS",
    compress_mode: str = "gzip",
    started_at: str = "2026-06-01 07:30:06",
    finished_at: str | None = "2026-06-01 07:30:10",
    expires_at: str | None = "2026-06-08 07:30:10",
    duration_seconds: int = 4,
    task_status: str = "succeeded",
    task_error: str | None = None,
    valid_status: str = "valid",
    remark: str = "自动备份",
) -> dict:
    return {
        "backup_id": backup_id,
        "task_id": f"task-{backup_id}",
        "service_name": service_name,
        "service_type": service_type,
        "child_service_name": child_service_name,
        "child_service_type": child_service_type,
        "unit_name": unit_name,
        "backup_type": backup_type,
        "backup_path": backup_path or f"/BACKUP/{service_name}/{backup_id}",
        "size_bytes": size_bytes,
        "storage_type": storage_type,
        "compress_mode": compress_mode,
        "started_at": started_at,
        "finished_at": finished_at,
        "expires_at": expires_at,
        "duration_seconds": duration_seconds,
        "task_status": task_status,
        "task_error": task_error,
        "valid_status": valid_status,
        "remark": remark,
    }


def _mysql_xf2_backup_records() -> list[dict]:
    return [
        _backup(
            "backup-mysql-xf2-20260524-primary",
            "mysql-xf2",
            started_at="2026-05-24 01:00:00",
            finished_at="2026-05-24 01:02:30",
            expires_at="2026-06-24 01:02:30",
            duration_seconds=150,
        ),
        _backup(
            "backup-mysql-xf2-20260527-primary",
            "mysql-xf2",
            started_at="2026-05-27 02:00:00",
            finished_at="2026-05-27 02:02:00",
            expires_at="2026-06-03 02:02:00",
            duration_seconds=120,
        ),
        _backup(
            "backup-mysql-xf2-20260529-primary-failed",
            "mysql-xf2",
            started_at="2026-05-29 02:00:00",
            finished_at="2026-05-29 02:10:00",
            expires_at="2026-06-05 02:10:00",
            duration_seconds=600,
            task_status="failed",
            task_error="xtrabackup timeout",
        ),
        _backup(
            "backup-mysql-xf2-20260530-replica-expired",
            "mysql-xf2",
            child_service_name="mysql-replica",
            unit_name="mysql-replica-01",
            started_at="2026-05-30 03:00:00",
            finished_at="2026-05-30 03:05:00",
            expires_at="2026-05-31 03:05:00",
            duration_seconds=300,
            remark="已过期但未删除",
        ),
        _backup(
            "backup-mysql-xf2-20260531-primary",
            "mysql-xf2",
            started_at="2026-05-31 21:30:00",
            finished_at="2026-05-31 21:33:00",
            expires_at="2026-06-07 21:33:00",
            duration_seconds=180,
        ),
        _backup(
            "backup-mysql-xf2-20260601-replica-failed",
            "mysql-xf2",
            child_service_name="mysql-replica",
            unit_name="mysql-replica-01",
            started_at="2026-06-01 01:00:00",
            finished_at="2026-06-01 01:03:00",
            expires_at="2026-06-08 01:03:00",
            duration_seconds=180,
            task_status="failed",
            task_error="S3 upload failed",
        ),
        _backup(
            "backup-mysql-xf2-20260601-replica",
            "mysql-xf2",
            child_service_name="mysql-replica",
            unit_name="mysql-replica-01",
            started_at="2026-06-01 08:15:00",
            finished_at="2026-06-01 08:17:00",
            expires_at="2026-06-08 08:17:00",
            duration_seconds=120,
        ),
        _backup(
            "backup-redis-cache-20260601-primary",
            "redis-cache",
            service_type="redis",
            child_service_name="redis-primary",
            child_service_type="redis",
            unit_name="redis-primary-01",
            started_at="2026-06-01 06:00:00",
            finished_at="2026-06-01 06:01:00",
            expires_at="2026-06-08 06:01:00",
            duration_seconds=60,
        ),
    ]


def _many_backup_records() -> list[dict]:
    records: list[dict] = []
    for index in range(75):
        day = 1 + index // 24
        hour = index % 24
        records.append(
            _backup(
                f"backup-mysql-xf2-bulk-{index:03d}",
                "mysql-xf2",
                started_at=f"2026-05-{day:02d} {hour:02d}:00:00",
                finished_at=f"2026-05-{day:02d} {hour:02d}:02:00",
                expires_at=f"2026-06-{min(day, 28):02d} {hour:02d}:02:00",
                duration_seconds=120,
                size_bytes=2_000_000 + index * 2048,
            )
        )
    return records


def _config(tmpdir: str) -> DbaasConfig:
    return DbaasConfig(
        server_base_url="http://127.0.0.1:9000",
        request_timeout_seconds=5,
        workspace_dir=Path(tmpdir),
        sync_interval_seconds=5,
        ttl_seconds=30,
        user_active_idle_timeout_seconds=300,
        user_snapshot_refresh_wait_seconds=3,
        jq_timeout_seconds=3,
        jq_max_preview_items=50,
        jq_max_output_bytes=16384,
        metric_snapshot_ttl_seconds=30,
        metric_snapshot_cleanup_interval_seconds=600,
        metric_refresh_lock_timeout_seconds=10,
    )


def _write_fresh_admin_snapshot(config: DbaasConfig, payload: list[dict]) -> None:
    workspace = DbaasWorkspace(config)
    data_path = workspace.data_path(BACKUPS_KIND)
    meta_path = workspace.meta_path(BACKUPS_KIND)
    write_json_atomic(data_path, payload)
    write_meta_atomic(
        meta_path,
        {
            "kind": BACKUPS_KIND,
            "scope": "admin",
            "user": None,
            "version": 1,
            "data_path": str(data_path),
            "meta_path": str(meta_path),
            "status": "fresh",
            "synced_at": "2026-06-01T10:00:00Z",
            "expires_at": "2099-06-01T10:00:30Z",
            "ttl_seconds": 30,
            "record_count": len(payload),
            "bytes": 1,
            "source": "dbaas-server",
            "source_endpoint": "/backups",
            "schema_version": "backups.v1",
            "schema_path": str((APP_ROOT / "config/schemas/backups.v1.schema.json").resolve()),
            "last_refresh_status": "success",
            "last_error": None,
        },
    )

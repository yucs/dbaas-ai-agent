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

from dbass_ai_agent.config import APP_ROOT  # noqa: E402
from dbass_ai_agent.dbaas.config import DbaasConfig  # noqa: E402
from dbass_ai_agent.dbaas.constants import SERVICES_KIND  # noqa: E402
from dbass_ai_agent.dbaas.locks import ResourceFileLock  # noqa: E402
from dbass_ai_agent.dbaas.query import query_dbaas_data  # noqa: E402
from dbass_ai_agent.dbaas.schema import describe_schema, validate_payload  # noqa: E402
from dbass_ai_agent.dbaas.sync import DbaasServiceSynchronizer, isoformat, utcnow  # noqa: E402
from dbass_ai_agent.dbaas.tools import (  # noqa: E402
    DbaasToolRunState,
    _apply_refreshing_retry_limit,
    dbaas_tool_identity,
)
from dbass_ai_agent.dbaas.visibility import ensure_visible_services  # noqa: E402
from dbass_ai_agent.dbaas.workspace import DbaasWorkspace, write_json_atomic, write_meta_atomic  # noqa: E402
from dbass_ai_agent.identity.models import Identity  # noqa: E402


class DbaasSchemaTests(unittest.TestCase):
    def test_services_schema_accepts_service_list(self) -> None:
        validate_payload(SERVICES_KIND, [_service("mysql-xf2", "payment-team")], app_root=APP_ROOT)

    def test_describe_schema_returns_top_level_summary(self) -> None:
        summary = describe_schema(SERVICES_KIND, app_root=APP_ROOT)

        self.assertEqual(summary["schema_version"], "services.v1")
        self.assertEqual(summary["top_level_type"], "array")
        self.assertTrue(any(field["name"] == "healthStatus" for field in summary["fields"]))


class DbaasSyncTests(unittest.TestCase):
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

    def test_visible_services_filters_regular_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            with patch.object(
                DbaasServiceSynchronizer,
                "_fetch_services",
                return_value=[
                    _service("mysql-a", "payment-team"),
                    _service("mysql-b", "billing-team"),
                ],
            ):
                meta = ensure_visible_services(
                    config,
                    Identity(user_id="alice", role="user", user="payment-team"),
                )

            payload = _read_json(Path(meta["data_path"]))
            self.assertEqual(meta["scope"], "user")
            self.assertEqual(meta["record_count"], 1)
            self.assertEqual([item["name"] for item in payload], ["mysql-a"])

    @unittest.skipUnless(shutil.which("jq"), "jq is required for DBAAS query tests")
    def test_query_dbaas_data_uses_user_scoped_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _config(tmpdir)
            _write_fresh_admin_snapshot(
                config,
                [
                    _service("mysql-a", "payment-team", health_status="HEALTHY"),
                    _service("mysql-b", "payment-team", health_status="UNHEALTHY"),
                    _service("mysql-c", "billing-team", health_status="UNHEALTHY"),
                ],
            )

            result = query_dbaas_data(
                config,
                Identity(user_id="alice", role="user", user="payment-team"),
                kind="services",
                jq_filter='.[] | select(.healthStatus != "HEALTHY") | {name, healthStatus}',
                max_preview_items=10,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["scope"], "user")
            self.assertEqual(result["preview"], [{"name": "mysql-b", "healthStatus": "UNHEALTHY"}])

    def test_tool_identity_context_can_exit_from_different_context(self) -> None:
        manager = dbaas_tool_identity(Identity(user_id="admin", role="admin", user=None))

        copy_context().run(manager.__enter__)
        copy_context().run(manager.__exit__, None, None, None)

    def test_resource_lock_removes_stale_pid_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "services.lock"
            lock_path.write_text("12345", encoding="ascii")

            with patch("dbass_ai_agent.dbaas.locks._pid_exists", return_value=False):
                with ResourceFileLock(lock_path, timeout_seconds=1):
                    self.assertTrue(lock_path.exists())

            self.assertFalse(lock_path.exists())

    def test_refreshing_retry_limit_stops_after_three_attempts(self) -> None:
        state = DbaasToolRunState()
        meta = {"kind": SERVICES_KIND, "status": "refreshing", "message": "refreshing"}

        statuses = [
            _apply_refreshing_retry_limit(meta, state, kind=SERVICES_KIND)["status"]
            for _ in range(4)
        ]

        self.assertEqual(
            statuses,
            [
                "refreshing",
                "refreshing",
                "refreshing",
                "refreshing_retry_exhausted",
            ],
        )

    def test_refreshing_retry_limit_resets_after_fresh_meta(self) -> None:
        state = DbaasToolRunState()
        refreshing = {"kind": SERVICES_KIND, "status": "refreshing", "message": "refreshing"}
        fresh = {"kind": SERVICES_KIND, "status": "fresh"}

        _apply_refreshing_retry_limit(refreshing, state, kind=SERVICES_KIND)
        _apply_refreshing_retry_limit(fresh, state, kind=SERVICES_KIND)
        result = _apply_refreshing_retry_limit(refreshing, state, kind=SERVICES_KIND)

        self.assertEqual(result["status"], "refreshing")
        self.assertEqual(result["refreshing_attempt"], 1)


def _config(tmpdir: str) -> DbaasConfig:
    return DbaasConfig(
        server_base_url="http://127.0.0.1:9000",
        request_timeout_seconds=1,
        workspace_dir=Path(tmpdir) / "workspace",
        sync_interval_seconds=5,
        ttl_seconds=30,
        resource_lock_timeout_seconds=1,
        jq_timeout_seconds=2,
        jq_max_preview_items=50,
        jq_max_output_bytes=1024 * 1024,
    )


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
            "schema_version": "services.v1",
        },
    )


def _read_json(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

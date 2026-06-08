import json
from pathlib import Path
import re
import time

from fastapi.testclient import TestClient

from app.main import create_app


HEX_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def create_test_client(task_unit_interval_seconds: float = 0.01) -> TestClient:
    app = create_app(task_unit_interval_seconds=task_unit_interval_seconds)
    return TestClient(app)


def admin_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer admin",
        "X-DBAAS-Actor-User": "admin",
        "X-DBAAS-Actor-Role": "admin",
    }


def user_headers(user: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer user",
        "X-DBAAS-Actor-User": user,
        "X-DBAAS-Actor-Role": "user",
    }


def wait_for_task_completion(client: TestClient, task_id: str, timeout_seconds: float = 1.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/tasks/{task_id}", headers=admin_headers())
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] != "RUNNING":
            return last_payload
        time.sleep(0.01)
    raise AssertionError(f"task '{task_id}' did not complete in time: {last_payload}")


def test_backup_capabilities_by_service_type_returns_simple_fields() -> None:
    client = create_test_client()

    response = client.get("/backup-task-capabilities?serviceType=mysql", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["serviceType"] == "mysql"
    assert payload["scopeValues"] == ["service", "unit"]
    fields = {item["name"]: item for item in payload["fields"]}
    assert fields["scope"]["requiresUserInput"] is True
    assert fields["scope"]["enumValues"] == ["service", "unit"]
    assert fields["backupType"]["requiresUserInput"] is True
    assert fields["retentionDays"]["requiresUserInput"] is True
    assert fields["options.compressMode"]["requiresUserInput"] is True
    assert all("default" not in item for item in payload["fields"])
    assert payload["resolvedTarget"]["scope"] == "service_type"
    assert payload["runtimeHints"]["backupRunning"] is False


def test_backup_capabilities_by_unit_reports_running_backups() -> None:
    client = create_test_client()

    response = client.get("/backup-task-capabilities?unitName=18be9e82_ordad002", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["resolvedTarget"]["serviceName"] == "ordad002"
    assert payload["resolvedTarget"]["unitName"] == "18be9e82_ordad002"
    assert payload["runtimeHints"]["backupRunning"] is True
    assert any(item["unitName"] == "18be9e82_ordad002" for item in payload["runtimeHints"]["runningBackups"])


def test_create_service_backup_task_creates_running_records_then_completes() -> None:
    client = create_test_client(task_unit_interval_seconds=0.2)

    create_response = client.post(
        "/services/payad001/backup",
        headers=admin_headers(),
        json={
            "scope": "unit",
            "backupType": "full",
            "retentionDays": 7,
            "unitName": "aaa8ee1f_payad001",
            "options": {"compressMode": "gzip"},
            "remark": "manual backup",
        },
    )

    assert create_response.status_code == 200
    task_id = create_response.json()["taskId"]
    assert HEX_ID_PATTERN.fullmatch(task_id)

    backups_response = client.get("/backups", headers=admin_headers())
    assert backups_response.status_code == 200
    created = [item for item in backups_response.json() if item["task_id"] == task_id]
    assert len(created) == 1
    assert HEX_ID_PATTERN.fullmatch(created[0]["backup_id"])
    assert created[0]["task_status"] == "running"
    assert created[0]["unit_name"] == "aaa8ee1f_payad001"

    task_payload = wait_for_task_completion(client, task_id)
    assert task_payload["type"] == "service.backup.create"
    assert task_payload["status"] == "SUCCESS"
    assert task_payload["result"]["backupIds"] == [created[0]["backup_id"]]

    refreshed_response = client.get("/backups", headers=admin_headers())
    refreshed = [item for item in refreshed_response.json() if item["task_id"] == task_id]
    assert len(refreshed) == 1
    assert refreshed[0]["task_status"] == "succeeded"
    assert refreshed[0]["expires_at"] is not None


def test_create_service_backup_task_for_service_can_create_multiple_records() -> None:
    client = create_test_client()

    create_response = client.post(
        "/services/payad001/backup",
        headers=admin_headers(),
        json={
            "scope": "service",
            "backupType": "full",
            "retentionDays": 3,
        },
    )

    assert create_response.status_code == 200
    task_id = create_response.json()["taskId"]
    assert HEX_ID_PATTERN.fullmatch(task_id)
    response = client.get("/backups", headers=admin_headers())
    created = [item for item in response.json() if item["task_id"] == task_id]
    assert len(created) >= 3
    assert all(HEX_ID_PATTERN.fullmatch(item["backup_id"]) for item in created)
    assert {item["task_status"] for item in created} == {"running"}


def test_create_service_backup_task_rejects_child_service_scope() -> None:
    client = create_test_client()

    response = client.post(
        "/services/payad001/backup",
        headers=admin_headers(),
        json={
            "scope": "child_service",
            "backupType": "full",
            "retentionDays": 3,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "scope must be one of service, unit"


def test_backup_seed_is_large_and_covers_core_scenarios() -> None:
    seed_path = Path(__file__).resolve().parents[1] / "data" / "backups.json"
    payload = json.loads(seed_path.read_text(encoding="utf-8"))

    assert seed_path.stat().st_size >= 3_000_000
    assert len(payload) >= 6_500
    statuses = {item["task_status"] for item in payload}
    assert {"succeeded", "failed", "timeout", "canceled", "running"}.issubset(statuses)
    assert any(item["storage_type"] is None for item in payload)
    assert any(item["finished_at"] is None for item in payload)
    assert any(item["expires_at"] is not None and item["remark"] == "已过期但未删除" for item in payload)
    assert any(item["deleted"] is True for item in payload)
    assert all(HEX_ID_PATTERN.fullmatch(item["backup_id"]) for item in payload)
    assert all(HEX_ID_PATTERN.fullmatch(item["task_id"]) for item in payload)
    assert len({item["backup_id"] for item in payload}) == len(payload)


def test_admin_can_query_all_existing_backups_including_expired_but_not_deleted() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) >= 6_200
    assert all(HEX_ID_PATTERN.fullmatch(item["backup_id"]) for item in payload)
    assert all(item.get("deleted") is not True for item in payload)
    assert any(item["service_name"] == "payad001" for item in payload)
    assert any(item["service_name"] == "ordad002" for item in payload)
    expired = next(item for item in payload if item["service_name"] == "payad001" and item["remark"] == "已过期但未删除")
    assert expired["remark"] == "已过期但未删除"
    statuses = {item["task_status"] for item in payload}
    assert {"succeeded", "failed", "timeout", "canceled", "running"}.issubset(statuses)


def test_non_admin_can_only_query_owned_backups() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=user_headers("payment-platform-team"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 7
    assert all(HEX_ID_PATTERN.fullmatch(item["backup_id"]) for item in payload)
    assert {item["service_name"] for item in payload} == {"payad001"}
    assert all("owner_user" not in item for item in payload)


def test_non_admin_cannot_see_other_users_backups() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=user_headers("db-platform-team"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert {item["service_name"] for item in payload} == {"ordad002"}
    assert {item["task_status"] for item in payload} == {"running", "succeeded", "failed"}

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def create_test_client() -> TestClient:
    app = create_app(task_unit_interval_seconds=0.01)
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


def test_admin_can_query_all_existing_backups_including_expired_but_not_deleted() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    backup_ids = {item["backup_id"] for item in payload}
    assert len(payload) >= 6_200
    assert "backup-mysql-xf2-001" in backup_ids
    assert "backup-mysql-xf2-002" in backup_ids
    assert "backup-tidb-oltp-001" in backup_ids
    assert "backup-redis-cache-deleted" not in backup_ids
    expired = next(item for item in payload if item["backup_id"] == "backup-mysql-xf2-002")
    assert expired["remark"] == "已过期但未删除"
    statuses = {item["task_status"] for item in payload}
    assert {"succeeded", "failed", "timeout", "canceled", "running"}.issubset(statuses)


def test_non_admin_can_only_query_owned_backups() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=user_headers("payment-platform-team"))

    assert response.status_code == 200
    payload = response.json()
    backup_ids = {item["backup_id"] for item in payload}
    assert len(payload) == 7
    assert {
        "backup-mysql-xf2-001",
        "backup-mysql-xf2-002",
        "backup-mysql-xf2-003",
        "backup-mysql-xf2-004",
        "backup-mysql-xf2-005",
        "backup-mysql-xf2-006",
        "backup-mysql-xf2-007",
    } == backup_ids
    assert all("owner_user" not in item for item in payload)


def test_non_admin_cannot_see_other_users_backups() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=user_headers("db-platform-team"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert {item["service_name"] for item in payload} == {"tidb-oltp"}
    assert {item["task_status"] for item in payload} == {"running", "succeeded", "failed"}

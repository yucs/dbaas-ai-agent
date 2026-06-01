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


def test_admin_can_query_all_existing_backups_including_expired_but_not_deleted() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    backup_ids = {item["backup_id"] for item in payload}
    assert "backup-mysql-xf2-001" in backup_ids
    assert "backup-mysql-xf2-002" in backup_ids
    assert "backup-tidb-oltp-001" in backup_ids
    assert "backup-redis-cache-deleted" not in backup_ids
    expired = next(item for item in payload if item["backup_id"] == "backup-mysql-xf2-002")
    assert expired["remark"] == "已过期但未删除"


def test_non_admin_can_only_query_owned_backups() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=user_headers("payment-platform-team"))

    assert response.status_code == 200
    payload = response.json()
    backup_ids = {item["backup_id"] for item in payload}
    assert backup_ids == {"backup-mysql-xf2-001", "backup-mysql-xf2-002"}
    assert all("owner_user" not in item for item in payload)


def test_non_admin_cannot_see_other_users_backups() -> None:
    client = create_test_client()

    response = client.get("/backups", headers=user_headers("db-platform-team"))

    assert response.status_code == 200
    payload = response.json()
    assert [item["backup_id"] for item in payload] == ["backup-tidb-oltp-001"]

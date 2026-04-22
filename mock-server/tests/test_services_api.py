import time

from fastapi.testclient import TestClient

from app.main import create_app


def create_test_client(task_unit_interval_seconds: float = 0.01) -> TestClient:
    app = create_app(task_unit_interval_seconds=task_unit_interval_seconds)
    return TestClient(app)


def wait_for_task_completion(client: TestClient, task_id: str, timeout_seconds: float = 1.0) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] != "RUNNING":
            return last_payload
        time.sleep(0.01)
    raise AssertionError(f"task '{task_id}' did not complete in time: {last_payload}")


def test_update_service_resource_updates_cpu_memory_and_platform_auto() -> None:
    client = create_test_client()

    response = client.put(
        "/services/mysql-xf2/resource",
        json={
            "childServiceType": "mysql",
            "platformAuto": False,
            "cpu": 16,
            "memory": 64,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    mysql_service = next(service for service in payload["services"] if service["type"] == "mysql")
    assert mysql_service["platformAuto"] is False
    assert all(unit["cpu"] == 16 for unit in mysql_service["units"])
    assert all(unit["memory"] == 64 for unit in mysql_service["units"])


def test_list_services_returns_all_loaded_service_groups() -> None:
    client = create_test_client()

    response = client.get("/services")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2208
    assert all(item["healthStatus"] in {"HEALTHY", "WARN", "UNHEALTHY"} for item in payload)
    assert any(item["healthStatus"] != "HEALTHY" for item in payload)
    assert all(item["siteId"].startswith("site-") for item in payload)
    service_names = {item["name"] for item in payload}
    assert "mysql-xf2" in service_names
    assert "tidb-oltp" in service_names
    assert any(name.endswith("-0001") for name in service_names)


def test_list_services_can_filter_by_owner() -> None:
    client = create_test_client()

    all_services = client.get("/services")
    assert all_services.status_code == 200
    all_payload = all_services.json()
    expected_owner_services = [
        item for item in all_payload if item["owner"] == "payment-team-prod"
    ]

    response = client.get("/services", params={"owner": "payment-team-prod"})

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert len(payload) == len(expected_owner_services)
    assert all(item["owner"] == "payment-team-prod" for item in payload)
    assert {item["name"] for item in payload} == {
        item["name"] for item in expected_owner_services
    }


def test_list_services_returns_empty_list_when_owner_has_no_matches() -> None:
    client = create_test_client()

    response = client.get("/services", params={"owner": "not-exist-owner"})

    assert response.status_code == 200
    assert response.json() == []


def test_get_service_can_load_additional_seed_samples() -> None:
    client = create_test_client()

    response = client.get("/services/tidb-oltp")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "tidb-oltp"
    assert payload["type"] == "tidb"
    assert payload["owner"] == "db-platform-team"
    assert payload["subsystem"] == "tidb-platform"
    assert payload["environment"] == "prod"
    assert payload["healthStatus"] == "HEALTHY"
    assert payload["siteId"].startswith("site-")
    assert payload["siteName"]
    assert payload["network"]["cidr"].startswith("192.168.")
    assert payload["backupStrategy"] == {
        "enabled": True,
        "type": "snapshot",
        "cronExpression": "0 0 1 * * *",
        "retention": 7,
        "compressMode": "zstd",
        "sendAlarm": True,
    }
    assert {service["type"] for service in payload["services"]} == {"tidb", "tikv", "pd"}
    tikv_service = next(service for service in payload["services"] if service["type"] == "tikv")
    assert tikv_service["healthStatus"] == "HEALTHY"
    assert len(tikv_service["units"]) == 3
    assert all(unit["healthStatus"] == "HEALTHY" for unit in tikv_service["units"])
    assert all(unit["containerStatus"] == "RUNNING" for unit in tikv_service["units"])
    assert all(unit["hostId"].startswith("host-") for unit in tikv_service["units"])
    assert all(unit["hostIp"].startswith("192.18.") for unit in tikv_service["units"])
    assert all(unit["containerIp"].startswith("192.168.") for unit in tikv_service["units"])
    assert all(unit["storage"]["data"]["diskId"].startswith(unit["hostId"]) for unit in tikv_service["units"])
    assert all(unit["storage"]["data"]["mediaType"] in {"SSD", "HDD"} for unit in tikv_service["units"])
    assert all(unit["storage"]["log"]["mediaType"] in {"SSD", "HDD"} for unit in tikv_service["units"])


def test_update_service_storage_updates_only_requested_storage_fields() -> None:
    client = create_test_client()

    response = client.put(
        "/services/mysql-xf2/storage",
        json={
            "childServiceType": "mysql",
            "storage": {
                "dataVolumeSize": 1024,
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    mysql_service = next(service for service in payload["services"] if service["type"] == "mysql")
    assert "platformAuto" not in mysql_service or mysql_service["platformAuto"] is None
    assert all(unit["storage"]["data"]["size"] == 1024 for unit in mysql_service["units"])
    assert all(unit["storage"]["log"]["size"] == 100 for unit in mysql_service["units"])


def test_update_service_resource_returns_404_when_service_not_found() -> None:
    client = create_test_client()

    response = client.put(
        "/services/not-exist/resource",
        json={
            "childServiceType": "mysql",
            "cpu": 4,
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "service 'not-exist' not found"}


def test_update_service_storage_returns_502_when_child_service_type_not_found() -> None:
    client = create_test_client()

    response = client.put(
        "/services/mysql-xf2/storage",
        json={
            "childServiceType": "redis",
            "storage": {
                "dataVolumeSize": 100,
            },
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "service 'mysql-xf2' has no child service type 'redis'"
    }


def test_update_service_resource_returns_422_when_no_update_fields_provided() -> None:
    client = create_test_client()

    response = client.put(
        "/services/mysql-xf2/resource",
        json={
            "childServiceType": "mysql",
        },
    )

    assert response.status_code == 422


def test_update_service_storage_returns_422_when_no_update_fields_provided() -> None:
    client = create_test_client()

    response = client.put(
        "/services/mysql-xf2/storage",
        json={
            "childServiceType": "mysql",
        },
    )

    assert response.status_code == 422


def test_create_image_upgrade_task_and_complete_via_task_query() -> None:
    client = create_test_client()

    create_response = client.post(
        "/services/mysql-xf2/image-upgrade",
        json={
            "childServiceType": "mysql",
            "image": "mysql:8.0.37",
            "version": "8.0.37",
            "unitIds": ["mysql-primary-01"],
        },
    )

    assert create_response.status_code == 200
    create_payload = create_response.json()
    assert list(create_payload.keys()) == ["taskId"]
    task_id = create_payload["taskId"]

    task_response = client.get(f"/tasks/{task_id}")
    assert task_response.status_code == 200
    running_payload = task_response.json()
    assert running_payload["type"] == "service.image.upgrade"
    assert running_payload["status"] == "RUNNING"
    assert running_payload["message"] == "image upgrade running"

    task_payload = wait_for_task_completion(client, task_id)
    assert task_payload["type"] == "service.image.upgrade"
    assert task_payload["status"] == "SUCCESS"
    assert task_payload["reason"] is None
    assert task_payload["message"] == "image upgrade completed"
    assert task_payload["result"] == {
        "childServiceType": "mysql",
        "unitIds": ["mysql-primary-01"],
        "image": "mysql:8.0.37",
        "version": "8.0.37",
    }

    service_response = client.get("/services/mysql-xf2")
    service_payload = service_response.json()
    mysql_service = next(service for service in service_payload["services"] if service["type"] == "mysql")
    primary_unit = next(unit for unit in mysql_service["units"] if unit["id"] == "mysql-primary-01")
    replica_unit = next(unit for unit in mysql_service["units"] if unit["id"] == "mysql-replica-01")
    assert primary_unit["image"] == "mysql:8.0.37"
    assert primary_unit["version"] == "8.0.37"
    assert replica_unit["image"] == "mysql"
    assert replica_unit["version"] == "8.0.36"


def test_create_image_upgrade_task_returns_400_when_unit_not_in_child_service() -> None:
    client = create_test_client()

    response = client.post(
        "/services/mysql-xf2/image-upgrade",
        json={
            "childServiceType": "mysql",
            "image": "mysql:8.0.37",
            "unitIds": ["proxy-01"],
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "service 'mysql-xf2' has no unit ids 'proxy-01' in child service type 'mysql'"
    }


def test_get_task_returns_404_when_task_not_found() -> None:
    client = create_test_client()

    response = client.get("/tasks/task-9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "task 'task-9999' not found"}


def test_image_upgrade_task_reports_progress_for_multiple_units() -> None:
    client = create_test_client(task_unit_interval_seconds=0.05)

    create_response = client.post(
        "/services/mysql-xf2/image-upgrade",
        json={
            "childServiceType": "mysql",
            "image": "mysql:8.0.37",
            "version": "8.0.37",
        },
    )

    assert create_response.status_code == 200
    task_id = create_response.json()["taskId"]

    deadline = time.time() + 1.0
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] == "SUCCESS":
            break
        time.sleep(0.01)

    assert last_payload is not None
    assert last_payload["status"] == "SUCCESS"
    assert last_payload["message"] == "image upgrade completed"

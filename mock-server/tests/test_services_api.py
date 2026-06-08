import re
import time

from fastapi.testclient import TestClient

from app.main import create_app


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


def get_first_user_service(client: TestClient, user: str) -> dict:
    response = client.get("/services", headers=user_headers(user))
    assert response.status_code == 200
    payload = response.json()
    assert payload
    return payload[0]


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


def test_update_service_resource_updates_cpu_memory_and_platform_auto() -> None:
    client = create_test_client()

    response = client.put(
        "/services/payad001/resource",
        headers=admin_headers(),
        json={
            "childServiceType": "mysql",
            "platformAuto": False,
            "cpu": 16,
            "memoryGB": 64,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    mysql_service = next(service for service in payload["childServices"] if service["type"] == "mysql")
    assert all(unit["cpu"] == 16 for unit in mysql_service["units"])
    assert all(unit["memoryGB"] == 64 for unit in mysql_service["units"])


def test_list_services_returns_all_loaded_service_groups() -> None:
    client = create_test_client()

    response = client.get("/services", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2208
    assert all(item["runningStatus"] in {"passing", "warning", "critical"} for item in payload)
    assert any(item["runningStatus"] != "passing" for item in payload)
    assert all(item["siteId"].startswith("site-") for item in payload)
    service_names = {item["name"] for item in payload}
    assert "payad001" in service_names
    assert "ordad002" in service_names
    assert any(re.fullmatch(r"[a-z]{3}[a-z]{2}\d{3}", name) for name in service_names)


def test_admin_service_units_do_not_expose_ids() -> None:
    client = create_test_client()

    response = client.get("/services", headers=admin_headers())

    assert response.status_code == 200
    unit_names = [
        unit["name"]
        for service_group in response.json()
        for child_service in service_group["childServices"]
        for unit in child_service["units"]
    ]
    assert unit_names
    assert all(
        "id" not in unit
        for service_group in response.json()
        for child_service in service_group["childServices"]
        for unit in child_service["units"]
    )
    assert len(unit_names) == len(set(unit_names))


def test_list_services_can_filter_by_user() -> None:
    client = create_test_client()

    all_services = client.get("/services", headers=admin_headers())
    assert all_services.status_code == 200
    all_payload = all_services.json()
    expected_user_services = [
        item for item in all_payload if item["user"] == "payment-team-prod"
    ]

    response = client.get(
        "/services",
        params={"user": "payment-team-prod"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert len(payload) == len(expected_user_services)
    assert all(item["user"] == "payment-team-prod" for item in payload)
    assert {item["name"] for item in payload} == {
        item["name"] for item in expected_user_services
    }


def test_list_services_returns_empty_list_when_user_has_no_matches() -> None:
    client = create_test_client()

    response = client.get(
        "/services",
        params={"user": "not-exist-user"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    assert response.json() == []


def test_get_service_can_load_additional_seed_samples() -> None:
    client = create_test_client()

    response = client.get("/services/ordad002", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "ordad002"
    assert payload["type"] == "tidb"
    assert payload["user"] == "db-platform-team"
    assert payload["subsystem"] == payload["businessSubsystemName"]
    assert payload["businessSubsystemName"] == "分布式订单库"
    assert payload["runningStatus"] == "passing"
    assert payload["businessSystemName"]
    assert payload["businessSubsystemName"]
    assert payload["siteId"].startswith("site-")
    assert payload["siteName"]
    assert "architecture" not in payload
    assert "architectureName" not in payload
    assert payload["backupStrategy"] == {
        "enabled": True,
        "type": "snapshot",
        "cronExpression": "0 0 1 * * *",
        "retention": 7,
        "compressMode": "zstd",
        "sendAlarm": True,
    }
    assert {service["type"] for service in payload["childServices"]} == {"tidb", "tikv", "pd"}
    tikv_service = next(service for service in payload["childServices"] if service["type"] == "tikv")
    assert {service["name"] for service in payload["childServices"]} == {
        "ordad002-tidb-01",
        "ordad002-tikv-01",
        "ordad002-pd-01",
    }
    assert tikv_service["runningStatus"] == "passing"
    assert len(tikv_service["units"]) == 3
    assert all(unit["runningStatus"] == "passing" for unit in tikv_service["units"])
    assert all(re.fullmatch(r"[0-9a-f]{8}_ordad002", unit["name"]) for unit in tikv_service["units"])
    assert all(unit["hostIp"].startswith("192.18.") for unit in tikv_service["units"])
    assert all(unit["ip"].startswith("192.168.") for unit in tikv_service["units"])
    assert all(unit["ipv6"].startswith("2405:78c0:") for unit in tikv_service["units"])
    assert all(unit["cpuArchitecture"] in {"amd64", "arm64"} for unit in tikv_service["units"])
    assert all(unit["cpuArchitectureDisplayName"] in {"X86", "ARM"} for unit in tikv_service["units"])
    assert all(unit["storage"]["data"]["type"] in {"local:SSD", "local:HDD"} for unit in tikv_service["units"])
    assert all(unit["storage"]["log"]["type"] in {"local:SSD", "local:HDD"} for unit in tikv_service["units"])


def test_update_service_storage_updates_only_requested_storage_fields() -> None:
    client = create_test_client()

    response = client.put(
        "/services/payad001/storage",
        headers=admin_headers(),
        json={
            "childServiceType": "mysql",
            "storage": {
                "data": {
                    "sizeGB": 1024,
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    mysql_service = next(service for service in payload["childServices"] if service["type"] == "mysql")
    assert all(unit["storage"]["data"]["sizeGB"] == 1024 for unit in mysql_service["units"])
    assert all(unit["storage"]["log"]["sizeGB"] == 100 for unit in mysql_service["units"])


def test_precheck_service_resource_update_returns_lightweight_facts() -> None:
    client = create_test_client()

    response = client.post(
        "/api/v1/prechecks/service-resource-update",
        headers=admin_headers(),
        json={
            "service_name": "payad001",
            "child_service_type": "mysql",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["service_name"] == "payad001"
    assert payload["child_service_type"] == "mysql"
    assert payload["current_spec"]["cpu_cores"] > 0
    assert payload["current_spec"]["memory_gb"] > 0
    assert payload["available_specs"]
    assert payload["runtime"]["unit_count"] == len(payload["metrics"]["units"])
    assert payload["metrics"]["time_window"] == "1d"
    first_metric = payload["metrics"]["units"][0]
    assert first_metric["cpu"]["latest"].endswith("%")
    assert first_metric["memory"]["avg"].endswith("%")
    assert payload["blocking_errors"] == []


def test_precheck_service_resource_update_reports_insufficient_capacity() -> None:
    client = create_test_client()

    response = client.post(
        "/api/v1/prechecks/service-resource-update",
        headers=admin_headers(),
        json={
            "service_name": "payad001",
            "child_service_type": "mysql",
            "target_cpu_cores": 101,
            "target_memory_gb": 301,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocking_errors"] == [
        {
            "code": "insufficient_capacity",
            "message": "当前主机或资源池资源不足，无法调整到目标值。",
        }
    ]


def test_precheck_service_storage_update_returns_lightweight_facts() -> None:
    client = create_test_client()

    response = client.post(
        "/api/v1/prechecks/service-storage-update",
        headers=admin_headers(),
        json={
            "service_name": "payad001",
            "child_service_type": "mysql",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["service_name"] == "payad001"
    assert payload["child_service_type"] == "mysql"
    assert payload["current_storage"]["data_volume_gb"] > 0
    assert payload["current_storage"]["log_volume_gb"] > 0
    assert payload["runtime"]["unit_count"] == len(payload["metrics"]["units"])
    first_metric = payload["metrics"]["units"][0]
    assert set(first_metric) == {"unit_name", "data_usage", "log_usage"}
    assert first_metric["data_usage"].endswith("%")
    assert first_metric["log_usage"].endswith("%")
    assert payload["blocking_errors"] == []


def test_precheck_service_storage_update_reports_insufficient_capacity() -> None:
    client = create_test_client()

    response = client.post(
        "/api/v1/prechecks/service-storage-update",
        headers=admin_headers(),
        json={
            "service_name": "payad001",
            "child_service_type": "mysql",
            "target_data_volume_gb": 2001,
            "target_log_volume_gb": 2001,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["blocking_errors"] == [
        {
            "code": "insufficient_capacity",
            "message": "当前存储池资源不足，无法调整到目标值。",
        }
    ]


def test_update_service_resource_returns_404_when_service_not_found() -> None:
    client = create_test_client()

    response = client.put(
        "/services/not-exist/resource",
        headers=admin_headers(),
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
        "/services/payad001/storage",
        headers=admin_headers(),
        json={
            "childServiceType": "redis",
            "storage": {
                "data": {
                    "sizeGB": 100,
                },
            },
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "service 'payad001' has no child service type 'redis'"
    }


def test_update_service_resource_returns_422_when_no_update_fields_provided() -> None:
    client = create_test_client()

    response = client.put(
        "/services/payad001/resource",
        headers=admin_headers(),
        json={
            "childServiceType": "mysql",
        },
    )

    assert response.status_code == 422


def test_update_service_storage_returns_422_when_no_update_fields_provided() -> None:
    client = create_test_client()

    response = client.put(
        "/services/payad001/storage",
        headers=admin_headers(),
        json={
            "childServiceType": "mysql",
        },
    )

    assert response.status_code == 422


def test_create_image_upgrade_task_and_complete_via_task_query() -> None:
    client = create_test_client()

    create_response = client.post(
        "/services/payad001/image-upgrade",
        headers=admin_headers(),
        json={
            "childServiceType": "mysql",
            "image": "mysql:8.0.37",
            "version": "8.0.37",
            "unitNames": ["aaa8ee1f_payad001"],
        },
    )

    assert create_response.status_code == 200
    create_payload = create_response.json()
    assert list(create_payload.keys()) == ["taskId"]
    task_id = create_payload["taskId"]
    assert re.fullmatch(
        r"[0-9a-f]{32}",
        task_id,
    )

    task_response = client.get(f"/tasks/{task_id}", headers=admin_headers())
    assert task_response.status_code == 200
    running_payload = task_response.json()
    assert running_payload["type"] == "service.image.upgrade"
    assert running_payload["status"] in {"RUNNING", "SUCCESS"}
    if running_payload["status"] == "RUNNING":
        assert running_payload["message"] == "image upgrade running"
    else:
        assert running_payload["message"] == "image upgrade completed"

    task_payload = wait_for_task_completion(client, task_id)
    assert task_payload["type"] == "service.image.upgrade"
    assert task_payload["status"] == "SUCCESS"
    assert task_payload["reason"] is None
    assert task_payload["message"] == "image upgrade completed"
    assert task_payload["result"] == {
        "childServiceType": "mysql",
        "unitNames": ["aaa8ee1f_payad001"],
        "image": "mysql:8.0.37",
        "version": "8.0.37",
    }

    service_response = client.get("/services/payad001", headers=admin_headers())
    service_payload = service_response.json()
    mysql_service = next(service for service in service_payload["childServices"] if service["type"] == "mysql")
    primary_unit = next(unit for unit in mysql_service["units"] if unit["name"] == "aaa8ee1f_payad001")
    replica_unit = next(unit for unit in mysql_service["units"] if unit["name"] == "6adbd13b_payad001")
    assert primary_unit["version"] == "8.0.37.1"
    assert replica_unit["version"].startswith("8.0.36")


def test_describe_image_upgrade_capabilities_returns_available_targets() -> None:
    client = create_test_client()

    response = client.get(
        "/image-upgrade-capabilities",
        headers=admin_headers(),
        params={
            "serviceName": "payad001",
            "childServiceType": "mysql",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "supported": True,
        "availableTargets": [
            {
                "image": "mysql:8.0.37",
                "version": "8.0.37",
            },
            {
                "image": "mysql:8.0.38",
                "version": "8.0.38",
            },
        ],
    }


def test_describe_image_upgrade_capabilities_enforces_service_access() -> None:
    client = create_test_client()

    response = client.get(
        "/image-upgrade-capabilities",
        headers=user_headers("order-platform-team"),
        params={
            "serviceName": "payad001",
            "childServiceType": "mysql",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "user 'order-platform-team' cannot access service 'payad001'",
    }


def test_describe_image_upgrade_capabilities_returns_502_for_missing_child_service() -> None:
    client = create_test_client()

    response = client.get(
        "/image-upgrade-capabilities",
        headers=admin_headers(),
        params={
            "serviceName": "payad001",
            "childServiceType": "tidb",
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "service 'payad001' has no child service type 'tidb'",
    }


def test_create_image_upgrade_task_returns_400_when_unit_not_in_child_service() -> None:
    client = create_test_client()

    response = client.post(
        "/services/payad001/image-upgrade",
        headers=admin_headers(),
        json={
            "childServiceType": "mysql",
            "image": "mysql:8.0.37",
            "unitNames": ["2637448a_payad001"],
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "service 'payad001' has no unit names '2637448a_payad001' in child service type 'mysql'"
    }


def test_get_task_returns_404_when_task_not_found() -> None:
    client = create_test_client()

    response = client.get("/tasks/task-9999", headers=admin_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "task 'task-9999' not found"}


def test_image_upgrade_task_reports_progress_for_multiple_units() -> None:
    client = create_test_client(task_unit_interval_seconds=0.05)

    create_response = client.post(
        "/services/payad001/image-upgrade",
        headers=admin_headers(),
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
        response = client.get(f"/tasks/{task_id}", headers=admin_headers())
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] == "SUCCESS":
            break
        time.sleep(0.01)

    assert last_payload is not None
    assert last_payload["status"] == "SUCCESS"
    assert last_payload["message"] == "image upgrade completed"


def test_business_endpoints_require_bearer_token() -> None:
    client = create_test_client()

    response = client.get("/services")

    assert response.status_code == 401
    assert response.json() == {"detail": "missing bearer token"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_list_services_for_user_only_returns_user_services() -> None:
    client = create_test_client()

    response = client.get("/services", headers=user_headers("payment-team-prod"))

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert all(item["user"] == "payment-team-prod" for item in payload)
    first = payload[0]
    assert "siteId" not in first
    first_unit = first["childServices"][0]["units"][0]
    assert "id" not in first_unit
    assert "hostId" not in first_unit
    assert "hostName" not in first_unit
    assert "hostIp" not in first_unit
    assert "diskId" not in first_unit["storage"]["data"]
    assert "diskName" not in first_unit["storage"]["data"]


def test_user_cannot_query_other_user_services() -> None:
    client = create_test_client()

    response = client.get(
        "/services",
        params={"user": "search-team-staging"},
        headers=user_headers("payment-team-prod"),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "user 'payment-team-prod' cannot query services for user 'search-team-staging'"
    }


def test_user_can_only_access_user_service_detail() -> None:
    client = create_test_client()
    owned_service = get_first_user_service(client, "payment-team-prod")

    own_response = client.get(
        f"/services/{owned_service['name']}",
        headers=user_headers("payment-team-prod"),
    )
    forbidden_response = client.get("/services/ordad002", headers=user_headers("payment-team-prod"))

    assert own_response.status_code == 200
    assert own_response.json()["user"] == "payment-team-prod"
    own_payload = own_response.json()
    assert "siteId" not in own_payload
    own_unit = own_payload["childServices"][0]["units"][0]
    assert "id" not in own_unit
    assert "hostId" not in own_unit
    assert "hostName" not in own_unit
    assert "hostIp" not in own_unit
    assert "diskId" not in own_unit["storage"]["data"]
    assert "diskName" not in own_unit["storage"]["data"]
    assert forbidden_response.status_code == 403
    assert forbidden_response.json() == {
        "detail": "user 'payment-team-prod' cannot access service 'ordad002'"
    }


def test_user_cannot_update_other_users_service() -> None:
    client = create_test_client()

    response = client.put(
        "/services/ordad002/resource",
        headers=user_headers("payment-team-prod"),
        json={
            "childServiceType": "tidb",
            "cpu": 8,
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "user 'payment-team-prod' cannot access service 'ordad002'"
    }


def test_user_cannot_query_task_for_other_users_service() -> None:
    client = create_test_client()
    owned_service = get_first_user_service(client, "payment-team-prod")
    service_detail_response = client.get(
        f"/services/{owned_service['name']}",
        headers=user_headers("payment-team-prod"),
    )
    assert service_detail_response.status_code == 200
    child_service_type = service_detail_response.json()["childServices"][0]["type"]

    create_response = client.post(
        f"/services/{owned_service['name']}/image-upgrade",
        headers=user_headers("payment-team-prod"),
        json={
            "childServiceType": child_service_type,
            "image": "mysql:8.0.37",
        },
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["taskId"]

    response = client.get(f"/tasks/{task_id}", headers=user_headers("search-team-staging"))

    assert response.status_code == 403
    assert response.json() == {
        "detail": f"user 'search-team-staging' cannot access service '{owned_service['name']}'"
    }

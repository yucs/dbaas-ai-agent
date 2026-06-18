import time

from fastapi.testclient import TestClient

from app.main import create_app


def create_test_client(task_unit_interval_seconds: float = 0.01) -> TestClient:
    app = create_app(task_unit_interval_seconds=task_unit_interval_seconds)
    return TestClient(app)


def admin_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer admin",
        "X-DBAAS-Actor-User": "ops-admin",
        "X-DBAAS-Actor-Role": "admin",
    }


def user_headers(user: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer user",
        "X-DBAAS-Actor-User": user,
        "X-DBAAS-Actor-Role": "user",
    }


CONTAINER_CPU_METRIC = "container.docker.cpu.avg_usage"
CONTAINER_MEMORY_METRIC = "container.docker.mem.usage"
MYSQL_STATUS_METRIC = "instance.upsql.running.status"
PAYAD_MYSQL_UNIT = "aaa8ee1f_payad001"
PAYAD_PROXY_UNIT = "2637448a_payad001"
OWNER_ACCOUNT_USER = "03000647"
OWNER_ACCOUNT_SERVICE = "accad119"
OWNER_ACCOUNT_MYSQL_UNIT = "373e0bd6_accad119"


def test_admin_can_query_latest_metric_with_100k_points_and_real_units() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": CONTAINER_CPU_METRIC},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 100_000
    assert any(
        item["service_name"] == "payad001"
        and item["unit_name"] == PAYAD_MYSQL_UNIT
        and item["service_type"] == "mysql"
        for item in payload
    )
    assert all(isinstance(item["value"], (int, float)) for item in payload[:100])
    assert any(item["service_name"].startswith("mock-svc-") and item["unit_name"].startswith("mock-") for item in payload)


def test_non_admin_can_query_all_owned_services_latest_metric_without_service_name() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": CONTAINER_CPU_METRIC},
        headers=user_headers("payment-platform-team"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 5_000
    assert any(
        item["service_name"] == "payad001"
        and item["unit_name"] == PAYAD_MYSQL_UNIT
        and item["service_type"] == "mysql"
        for item in payload
    )
    assert any(
        item["service_name"] == "payad001"
        and item["unit_name"].startswith("payment-platform-team-mock-")
        for item in payload
    )


def test_non_admin_can_query_owner_account_services_latest_metric_without_service_name() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": CONTAINER_CPU_METRIC},
        headers=user_headers(OWNER_ACCOUNT_USER),
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 5_000
    assert any(item["service_name"] == OWNER_ACCOUNT_SERVICE for item in payload)
    assert any(
        item["service_name"] == OWNER_ACCOUNT_SERVICE
        and item["unit_name"].startswith(f"{OWNER_ACCOUNT_USER}-mock-")
        for item in payload
    )


def test_non_admin_can_query_own_service_latest_metric() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": CONTAINER_MEMORY_METRIC, "service_name": "payad001"},
        headers=user_headers("payment-platform-team"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 100_000
    assert payload[0]["service_name"] == "payad001"
    assert payload[0]["unit_name"] == PAYAD_PROXY_UNIT
    assert all(item["service_name"] == "payad001" for item in payload[:100])
    assert all(isinstance(item["value"], (int, float)) for item in payload[:100])


def test_non_admin_can_query_owner_account_service_latest_metric() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": CONTAINER_MEMORY_METRIC, "service_name": OWNER_ACCOUNT_SERVICE},
        headers=user_headers(OWNER_ACCOUNT_USER),
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 100_000
    assert all(item["service_name"] == OWNER_ACCOUNT_SERVICE for item in payload[:100])
    assert all(isinstance(item["value"], (int, float)) for item in payload[:100])


def test_non_admin_cannot_query_other_users_service_latest_metric() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": CONTAINER_CPU_METRIC, "service_name": "payad001"},
        headers=user_headers("db-platform-team"),
    )

    assert response.status_code == 403


def test_latest_metric_uses_catalog_value_types() -> None:
    client = create_test_client()

    replication_response = client.get(
        "/metrics/latest",
        params={"metric_key": MYSQL_STATUS_METRIC},
        headers=admin_headers(),
    )

    assert replication_response.status_code == 200
    assert replication_response.json()[0]["value"] in {"passing", "warning", "critical", "unknown"}


def test_unknown_latest_metric_key_returns_404() -> None:
    client = create_test_client()

    response = client.get(
        "/metrics/latest",
        params={"metric_key": "container.notExist"},
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "metric_key 'container.notExist' not found"}


def test_admin_can_query_unit_metric_history() -> None:
    client = create_test_client()
    end_ts = int(time.time()) - 60
    start_ts = end_ts - 300

    response = client.get(
        f"/units/{PAYAD_MYSQL_UNIT}/metrics/history",
        params={
            "metric_key": CONTAINER_CPU_METRIC,
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
        headers=admin_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["ts"] == start_ts
    assert isinstance(payload[0]["value"], (int, float))


def test_unit_metric_history_rejects_invalid_time_range() -> None:
    client = create_test_client()
    now_ts = int(time.time()) - 60

    response = client.get(
        f"/units/{PAYAD_MYSQL_UNIT}/metrics/history",
        params={
            "metric_key": CONTAINER_CPU_METRIC,
            "start_ts": now_ts,
            "end_ts": now_ts,
        },
        headers=admin_headers(),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "start_ts must be less than end_ts"}


def test_non_admin_can_query_own_real_unit_history() -> None:
    client = create_test_client()
    end_ts = int(time.time()) - 60
    start_ts = end_ts - 120

    response = client.get(
        f"/units/{PAYAD_MYSQL_UNIT}/metrics/history",
        params={
            "metric_key": MYSQL_STATUS_METRIC,
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
        headers=user_headers("payment-platform-team"),
    )

    assert response.status_code == 200
    assert isinstance(response.json()[0]["value"], str)


def test_non_admin_can_query_owner_account_real_unit_history() -> None:
    client = create_test_client()
    end_ts = int(time.time()) - 60
    start_ts = end_ts - 120

    response = client.get(
        f"/units/{OWNER_ACCOUNT_MYSQL_UNIT}/metrics/history",
        params={
            "metric_key": MYSQL_STATUS_METRIC,
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
        headers=user_headers(OWNER_ACCOUNT_USER),
    )

    assert response.status_code == 200
    assert isinstance(response.json()[0]["value"], str)


def test_unit_metric_history_rejects_fake_unit() -> None:
    client = create_test_client()
    end_ts = int(time.time()) - 60
    start_ts = end_ts - 120

    response = client.get(
        "/units/payad001-mock-000001/metrics/history",
        params={
            "metric_key": CONTAINER_CPU_METRIC,
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
        headers=admin_headers(),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "unit 'payad001-mock-000001' not found"}

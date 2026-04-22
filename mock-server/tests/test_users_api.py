from fastapi.testclient import TestClient

from app.main import create_app


def create_test_client() -> TestClient:
    app = create_app(task_unit_interval_seconds=0.01)
    return TestClient(app)


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin"}


def user_headers(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer user:{user}"}


def test_user_endpoints_require_bearer_token() -> None:
    client = create_test_client()

    response = client.get("/users")

    assert response.status_code == 401
    assert response.json() == {"detail": "missing bearer token"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_list_users_returns_all_known_users_for_admin() -> None:
    client = create_test_client()

    services_response = client.get("/services", headers=admin_headers())
    assert services_response.status_code == 200
    expected_users = sorted(
        {item["user"] for item in services_response.json() if item["user"] is not None}
    )

    response = client.get("/users", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert [item["user"] for item in payload] == expected_users
    assert all(item["serviceGroupCount"] > 0 for item in payload)
    assert any(item["user"] == "payment-team-prod" for item in payload)


def test_get_user_returns_aggregated_user_service_groups() -> None:
    client = create_test_client()

    services_response = client.get(
        "/services",
        params={"user": "payment-team-prod"},
        headers=admin_headers(),
    )
    assert services_response.status_code == 200
    user_services = services_response.json()

    response = client.get("/users/payment-team-prod", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"] == "payment-team-prod"
    assert payload["serviceGroupCount"] == len(user_services)
    assert payload["environments"] == sorted({item["environment"] for item in user_services})
    assert payload["subsystems"] == sorted({item["subsystem"] for item in user_services})
    assert [item["name"] for item in payload["serviceGroups"]] == [
        item["name"] for item in user_services
    ]
    assert all(item["user"] == "payment-team-prod" for item in payload["serviceGroups"])


def test_list_users_for_user_only_returns_self() -> None:
    client = create_test_client()

    response = client.get("/users", headers=user_headers("payment-team-prod"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["user"] == "payment-team-prod"
    assert payload[0]["serviceGroupCount"] > 0


def test_user_can_only_access_self_user_detail() -> None:
    client = create_test_client()

    own_response = client.get(
        "/users/payment-team-prod",
        headers=user_headers("payment-team-prod"),
    )
    forbidden_response = client.get(
        "/users/search-team-staging",
        headers=user_headers("payment-team-prod"),
    )

    assert own_response.status_code == 200
    assert own_response.json()["user"] == "payment-team-prod"
    assert forbidden_response.status_code == 403
    assert forbidden_response.json() == {
        "detail": "user 'payment-team-prod' cannot access user 'search-team-staging'"
    }


def test_get_user_returns_404_when_user_not_found() -> None:
    client = create_test_client()

    response = client.get("/users/not-exist-user", headers=admin_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "user 'not-exist-user' not found"}

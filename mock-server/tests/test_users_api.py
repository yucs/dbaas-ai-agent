from fastapi.testclient import TestClient

from app.main import create_app


def create_test_client() -> TestClient:
    app = create_app(task_unit_interval_seconds=0.01)
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


def test_user_endpoints_require_bearer_token() -> None:
    client = create_test_client()

    response = client.get("/users")

    assert response.status_code == 401
    assert response.json() == {"detail": "missing bearer token"}
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_user_token_requires_actor_headers() -> None:
    client = create_test_client()

    response = client.get("/users", headers={"Authorization": "Bearer user"})

    assert response.status_code == 401
    assert response.json() == {"detail": "missing X-DBAAS-Actor-User header"}


def test_admin_token_rejects_reserved_admin_actor_user() -> None:
    client = create_test_client()

    response = client.get(
        "/users",
        headers={
            "Authorization": "Bearer admin",
            "X-DBAAS-Actor-User": "admin",
            "X-DBAAS-Actor-Role": "admin",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "admin actor user must be a concrete user"}


def test_list_users_returns_all_known_users_for_admin() -> None:
    client = create_test_client()

    services_response = client.get("/services", headers=admin_headers())
    assert services_response.status_code == 200
    expected_users = sorted(
        {
            value
            for item in services_response.json()
            for value in (item.get("user"), item.get("ownerAccount"))
            if value is not None
        }
    )

    response = client.get("/users", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert [item["user"] for item in payload] == expected_users
    assert all(item["serviceGroupCount"] > 0 for item in payload)
    assert any(item["user"] == "payment-team-prod" for item in payload)
    assert any(item["user"] == "03000647" for item in payload)


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
    assert payload["environments"] == sorted(payload["environments"])
    assert payload["environments"]
    assert payload["subsystems"] == sorted({item["subsystem"] for item in user_services})
    assert [item["name"] for item in payload["serviceGroups"]] == [
        item["name"] for item in user_services
    ]
    assert all(item["user"] == "payment-team-prod" for item in payload["serviceGroups"])


def test_get_user_can_aggregate_service_groups_by_owner_account() -> None:
    client = create_test_client()

    services_response = client.get(
        "/services",
        params={"user": "03000647"},
        headers=admin_headers(),
    )
    assert services_response.status_code == 200
    user_services = services_response.json()

    response = client.get("/users/03000647", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["user"] == "03000647"
    assert payload["serviceGroupCount"] == len(user_services)
    assert payload["serviceGroupCount"] > 0
    assert [item["name"] for item in payload["serviceGroups"]] == [
        item["name"] for item in user_services
    ]


def test_list_users_for_user_only_returns_self() -> None:
    client = create_test_client()

    response = client.get("/users", headers=user_headers("payment-team-prod"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["user"] == "payment-team-prod"
    assert payload[0]["serviceGroupCount"] > 0


def test_list_users_for_owner_account_user_only_returns_self() -> None:
    client = create_test_client()

    response = client.get("/users", headers=user_headers("03000647"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["user"] == "03000647"
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

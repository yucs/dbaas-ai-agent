from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def create_test_client() -> TestClient:
    app = create_app(task_unit_interval_seconds=0.01)
    return TestClient(app)


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin"}


def user_headers(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer user:{user}"}


def test_seed_files_exist_with_normalized_platform_layout() -> None:
    data_dir = Path(__file__).resolve().parents[1] / "data"

    assert (data_dir / "sites.json").exists()
    assert (data_dir / "clusters.json").exists()
    assert (data_dir / "hosts.json").exists()
    assert (data_dir / "services.json").exists()


def test_list_sites_clusters_and_hosts_returns_platform_inventory() -> None:
    client = create_test_client()

    sites_response = client.get("/sites", headers=admin_headers())
    clusters_response = client.get("/clusters", headers=admin_headers())
    hosts_response = client.get("/hosts", headers=admin_headers())

    assert sites_response.status_code == 200
    assert clusters_response.status_code == 200
    assert hosts_response.status_code == 200

    sites = sites_response.json()
    clusters = clusters_response.json()
    hosts = hosts_response.json()

    assert len(sites) == 12
    assert len(clusters) == 48
    assert len(hosts) == 2880
    assert all(site["clusterCount"] == 4 for site in sites)
    assert any(site["healthStatus"] != "HEALTHY" for site in sites)
    assert all(host["ip"].startswith("192.18.") for host in hosts)
    assert all(len(host["disks"]) == 2 for host in hosts)
    assert any(host["healthStatus"] != "HEALTHY" for host in hosts)
    assert all(
        {disk["mediaType"] for disk in host["disks"]} == {"SSD", "HDD"}
        for host in hosts
    )


def test_get_site_returns_clusters_and_service_groups() -> None:
    client = create_test_client()

    response = client.get("/sites/site-prod-sh-01", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "site-prod-sh-01"
    assert payload["clusterCount"] == 4
    assert payload["hostCount"] == 240
    assert len(payload["clusters"]) == 4
    assert payload["serviceGroupCount"] >= 1
    assert len(payload["serviceGroups"]) >= 1


def test_get_cluster_returns_hosts_and_service_counts() -> None:
    client = create_test_client()

    response = client.get("/clusters/cluster-site-prod-sh-01-01", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "cluster-site-prod-sh-01-01"
    assert payload["hostCount"] == 60
    assert len(payload["hosts"]) == 60
    assert payload["serviceGroupCount"] >= 1


def test_get_host_returns_disk_and_unit_details() -> None:
    client = create_test_client()

    host_list_response = client.get("/hosts", headers=admin_headers())
    assert host_list_response.status_code == 200
    host_id = next(host["id"] for host in host_list_response.json() if host["unitCount"] > 0)

    response = client.get(f"/hosts/{host_id}", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == host_id
    assert payload["ip"].startswith("192.18.")
    assert payload["unitCount"] >= 1
    assert len(payload["disks"]) == 2
    assert {disk["mediaType"] for disk in payload["disks"]} == {"SSD", "HDD"}
    assert all(disk["type"] in {"data", "log"} for disk in payload["disks"])
    assert len(payload["units"]) >= 1
    assert all(unit["containerIp"].startswith("192.168.") for unit in payload["units"])
    assert all(unit["healthStatus"] in {"HEALTHY", "WARN", "UNHEALTHY"} for unit in payload["units"])
    assert all(unit["containerStatus"] in {"RUNNING", "RESTARTING", "STOPPED", "FAILED"} for unit in payload["units"])


def test_platform_endpoints_return_404_when_resource_not_found() -> None:
    client = create_test_client()

    assert client.get("/sites/not-exist", headers=admin_headers()).status_code == 404
    assert client.get("/clusters/not-exist", headers=admin_headers()).status_code == 404
    assert client.get("/hosts/not-exist", headers=admin_headers()).status_code == 404


def test_non_admin_user_cannot_access_platform_resources() -> None:
    client = create_test_client()

    sites_response = client.get("/sites", headers=user_headers("payment-team-prod"))
    clusters_response = client.get("/clusters", headers=user_headers("payment-team-prod"))
    hosts_response = client.get("/hosts", headers=user_headers("payment-team-prod"))

    assert sites_response.status_code == 403
    assert clusters_response.status_code == 403
    assert hosts_response.status_code == 403
    assert sites_response.json() == {
        "detail": "platform resources are only available to admin users"
    }

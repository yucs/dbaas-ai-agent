from pathlib import Path

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


def test_seed_files_exist_with_normalized_platform_layout() -> None:
    data_dir = Path(__file__).resolve().parents[1] / "data"

    assert (data_dir / "sites.json").exists()
    assert (data_dir / "clusters.json").exists()
    assert (data_dir / "network_segments.json").exists()
    assert (data_dir / "hosts.json").exists()
    assert (data_dir / "services.json").exists()


def test_list_sites_clusters_and_hosts_returns_platform_inventory() -> None:
    client = create_test_client()

    sites_response = client.get("/sites", headers=admin_headers())
    clusters_response = client.get("/clusters", headers=admin_headers())
    network_segments_response = client.get("/network-segments", headers=admin_headers())
    hosts_response = client.get("/hosts", headers=admin_headers())

    assert sites_response.status_code == 200
    assert clusters_response.status_code == 200
    assert network_segments_response.status_code == 200
    assert hosts_response.status_code == 200

    sites = sites_response.json()
    clusters = clusters_response.json()
    network_segments = network_segments_response.json()
    hosts = hosts_response.json()

    assert len(sites) == 12
    assert len(clusters) >= 100
    assert len(network_segments) >= 200
    assert len(hosts) == len(clusters) * 60
    assert all(site["clusterCount"] == 9 for site in sites)
    assert any(site["healthStatus"] != "HEALTHY" for site in sites)
    assert all("supportedSoftwareTypes" in cluster for cluster in clusters)
    assert all("supportedNetworkNames" in cluster for cluster in clusters)
    assert all("clusterType" not in cluster for cluster in clusters)
    assert any("mysql" in cluster["supportedSoftwareTypes"] for cluster in clusters)
    assert any("redis" in cluster["supportedSoftwareTypes"] for cluster in clusters)
    assert all(segment["name"] in {name for cluster in clusters for name in cluster["supportedNetworkNames"]} for segment in network_segments)
    assert all(segment["enabled"] in {True, False} for segment in network_segments)
    assert all(segment["ipv4UsagePercent"] <= 100 for segment in network_segments)
    assert all(segment["ipv6UsagePercent"] <= 100 for segment in network_segments)
    assert all(host["ip"].startswith("192.18.") for host in hosts)
    assert all((host["hdd"] is None) != (host["ssd"] is None) for host in hosts)
    assert all(host["cpuCapacityCores"] >= host["cpuAllocatedCores"] for host in hosts)
    assert all(host["memoryCapacityGB"] >= host["memoryAllocatedGB"] for host in hosts)
    assert any(host["healthStatus"] != "HEALTHY" for host in hosts)
    assert all("disks" not in host for host in hosts)


def test_get_site_returns_clusters_and_service_groups() -> None:
    client = create_test_client()

    sites_response = client.get("/sites", headers=admin_headers())
    assert sites_response.status_code == 200
    site_id = next(site["id"] for site in sites_response.json() if site["name"] == "上海PIT站")

    response = client.get(f"/sites/{site_id}", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == site_id
    assert payload["clusterCount"] == 9
    assert payload["hostCount"] == 540
    assert len(payload["clusters"]) == 9
    assert payload["serviceGroupCount"] >= 1
    assert len(payload["serviceGroups"]) >= 1


def test_get_cluster_returns_hosts_and_service_counts() -> None:
    client = create_test_client()

    clusters_response = client.get("/clusters", headers=admin_headers())
    assert clusters_response.status_code == 200
    cluster_id = next(cluster["id"] for cluster in clusters_response.json() if cluster["siteName"] == "上海PIT站")

    response = client.get(f"/clusters/{cluster_id}", headers=admin_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == cluster_id
    assert len(payload["hosts"]) == 60
    assert "supportedCpuArchitectures" in payload
    assert "supportedSoftwareTypes" in payload
    assert "supportedNetworkNames" in payload
    assert "hostCount" not in payload
    assert "serviceGroupCount" not in payload


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
    assert (payload["hdd"] is None) != (payload["ssd"] is None)
    assert payload["maxUnitCount"] >= payload["unitCount"]
    assert len(payload["units"]) >= 1
    assert all(unit["containerIp"].startswith("192.168.") for unit in payload["units"])
    assert all(unit["healthStatus"] in {"HEALTHY", "WARN", "UNHEALTHY"} for unit in payload["units"])
    assert all(unit["containerStatus"] in {"RUNNING", "RESTARTING", "STOPPED", "FAILED"} for unit in payload["units"])


def test_platform_endpoints_return_404_when_resource_not_found() -> None:
    client = create_test_client()

    assert client.get("/sites/not-exist", headers=admin_headers()).status_code == 404
    assert client.get("/clusters/not-exist", headers=admin_headers()).status_code == 404
    assert client.get("/network-segments/not-exist", headers=admin_headers()).status_code == 404
    assert client.get("/hosts/not-exist", headers=admin_headers()).status_code == 404


def test_non_admin_user_cannot_access_platform_resources() -> None:
    client = create_test_client()

    sites_response = client.get("/sites", headers=user_headers("payment-team-prod"))
    clusters_response = client.get("/clusters", headers=user_headers("payment-team-prod"))
    network_segments_response = client.get("/network-segments", headers=user_headers("payment-team-prod"))
    hosts_response = client.get("/hosts", headers=user_headers("payment-team-prod"))

    assert sites_response.status_code == 403
    assert clusters_response.status_code == 403
    assert network_segments_response.status_code == 403
    assert hosts_response.status_code == 403
    assert sites_response.json() == {
        "detail": "platform resources are only available to admin users"
    }

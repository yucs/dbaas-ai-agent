"""Generate normalized DBaaS seed data for the mock server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RNG = random.Random(20260421)
HOST_BY_ID: dict[str, dict[str, Any]] = {}

SITE_COUNT = 12
CLUSTERS_PER_SITE = 4
HOSTS_PER_CLUSTER = 60
GENERATED_SERVICE_COUNT = 2200
BACKUP_REFERENCE_TIME = datetime(2026, 6, 1, 10, 0, 0)
SPECIAL_BACKUP_SERVICE_NAMES = {
    "mysql-xf2",
    "tidb-oltp",
    "redis-cache",
}


@dataclass(frozen=True)
class SiteSpec:
    id: str
    name: str
    environment: str
    region: str
    zone: str
    sequence: int


SERVICE_PATTERNS: list[dict[str, Any]] = [
    {"type": "mysql", "name_prefix": "account-mysql", "user": "account-team-prod", "subsystem": "account", "environments": ("prod",), "weight": 18},
    {"type": "mysql", "name_prefix": "payment-mysql", "user": "payment-team-prod", "subsystem": "payment", "environments": ("prod", "staging"), "weight": 16},
    {"type": "mysql", "name_prefix": "billing-mysql", "user": "billing-team-prod", "subsystem": "billing", "environments": ("prod", "staging"), "weight": 12},
    {"type": "tidb", "name_prefix": "order-tidb", "user": "order-team-prod", "subsystem": "order", "environments": ("prod", "staging"), "weight": 14},
    {"type": "tidb", "name_prefix": "inventory-tidb", "user": "inventory-team-prod", "subsystem": "inventory", "environments": ("prod", "staging"), "weight": 12},
    {"type": "kafka", "name_prefix": "trade-kafka", "user": "trade-team-staging", "subsystem": "trade", "environments": ("staging", "perf"), "weight": 10},
    {"type": "kafka", "name_prefix": "message-kafka", "user": "messaging-team-staging", "subsystem": "messaging", "environments": ("staging", "perf"), "weight": 8},
    {"type": "influxdb", "name_prefix": "monitor-influxdb", "user": "monitor-team-prod", "subsystem": "monitor", "environments": ("prod", "perf"), "weight": 10},
    {"type": "redis", "name_prefix": "session-redis", "user": "session-team-prod", "subsystem": "session", "environments": ("prod", "staging", "dev"), "weight": 10},
    {"type": "redis", "name_prefix": "profile-redis", "user": "profile-team-prod", "subsystem": "profile", "environments": ("prod", "staging", "dev"), "weight": 8},
    {"type": "mongodb", "name_prefix": "content-mongodb", "user": "content-team-staging", "subsystem": "content", "environments": ("staging", "dev"), "weight": 8},
    {"type": "mongodb", "name_prefix": "growth-mongodb", "user": "growth-team-staging", "subsystem": "growth", "environments": ("staging", "dev"), "weight": 8},
    {"type": "elasticsearch", "name_prefix": "search-es", "user": "search-team-staging", "subsystem": "search", "environments": ("staging", "perf"), "weight": 6},
    {"type": "elasticsearch", "name_prefix": "recommend-es", "user": "recommend-team-staging", "subsystem": "recommend", "environments": ("staging", "perf"), "weight": 6},
    {"type": "clickhouse", "name_prefix": "warehouse-clickhouse", "user": "warehouse-team-prod", "subsystem": "warehouse", "environments": ("prod", "perf"), "weight": 6},
    {"type": "clickhouse", "name_prefix": "analytics-clickhouse", "user": "analytics-team-prod", "subsystem": "analytics", "environments": ("prod", "perf"), "weight": 6},
]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    sites = build_sites()
    clusters = build_clusters(sites)
    hosts = build_hosts(sites, clusters)
    services = build_services(sites, clusters, hosts)
    backups = build_backups(services)

    write_json(DATA_DIR / "sites.json", sites)
    write_json(DATA_DIR / "clusters.json", clusters)
    write_json(DATA_DIR / "hosts.json", hosts)
    write_json(DATA_DIR / "services.json", services)
    write_json(DATA_DIR / "backups.json", backups)


def build_sites() -> list[dict[str, Any]]:
    """Build raw site seed data."""

    site_rows = [
        ("site-prod-sh-01", "Shanghai Production Site 01", "prod", "cn-east-1", "cn-east-1a"),
        ("site-prod-sh-02", "Shanghai Production Site 02", "prod", "cn-east-1", "cn-east-1b"),
        ("site-prod-bj-01", "Beijing Production Site 01", "prod", "cn-north-1", "cn-north-1a"),
        ("site-prod-gz-01", "Guangzhou Production Site 01", "prod", "cn-south-1", "cn-south-1a"),
        ("site-staging-sh-01", "Shanghai Staging Site 01", "staging", "cn-east-1", "cn-east-1c"),
        ("site-staging-bj-01", "Beijing Staging Site 01", "staging", "cn-north-1", "cn-north-1b"),
        ("site-staging-gz-01", "Guangzhou Staging Site 01", "staging", "cn-south-1", "cn-south-1b"),
        ("site-dev-hz-01", "Hangzhou Development Site 01", "dev", "cn-east-2", "cn-east-2a"),
        ("site-dev-sz-01", "Shenzhen Development Site 01", "dev", "cn-south-2", "cn-south-2a"),
        ("site-perf-sh-01", "Shanghai Performance Site 01", "perf", "cn-east-1", "cn-east-1d"),
        ("site-perf-bj-01", "Beijing Performance Site 01", "perf", "cn-north-1", "cn-north-1c"),
        ("site-dr-sh-01", "Shanghai Disaster Recovery Site 01", "prod", "cn-east-1", "cn-east-1e"),
    ]

    sites: list[dict[str, Any]] = []
    for sequence, (site_id, name, environment, region, zone) in enumerate(site_rows):
        sites.append(
            {
                "id": site_id,
                "name": name,
                "environment": environment,
                "region": region,
                "zone": zone,
                "sequence": sequence,
                "siteType": "SELF_MANAGED",
                "provider": "DBScale Cloud",
                "healthStatus": "HEALTHY",
                "contactGroup": f"{environment}-sre",
            }
        )
    return sites


def build_clusters(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build raw cluster seed data."""

    clusters: list[dict[str, Any]] = []
    for site in sites:
        site_sequence = int(site["sequence"])
        for cluster_index in range(CLUSTERS_PER_SITE):
            sequence = site_sequence * CLUSTERS_PER_SITE + cluster_index + 1
            cluster_id = f"cluster-{site['id']}-{cluster_index + 1:02d}"
            cluster_type = ("KUBERNETES", "KUBERNETES", "BAREMETAL", "KUBERNETES")[cluster_index]
            scheduler = {"KUBERNETES": "K8S", "BAREMETAL": "SYSTEMD"}[cluster_type]
            clusters.append(
                {
                    "id": cluster_id,
                    "name": f"{site['name']} Cluster {cluster_index + 1:02d}",
                    "siteId": site["id"],
                    "sequence": sequence,
                    "clusterType": cluster_type,
                    "scheduler": scheduler,
                    "healthStatus": "HEALTHY",
                    "controlPlaneVersion": f"1.{26 + cluster_index}.{site_sequence % 5}",
                    "runtime": "containerd" if cluster_type == "KUBERNETES" else "systemd",
                    "networkMode": "overlay" if cluster_type == "KUBERNETES" else "underlay",
                }
            )
    return clusters


def build_hosts(sites: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build raw host seed data."""

    site_by_id = {site["id"]: site for site in sites}
    hosts: list[dict[str, Any]] = []
    for cluster in clusters:
        site = site_by_id[cluster["siteId"]]
        cluster_sequence = int(cluster["sequence"])
        for host_index in range(HOSTS_PER_CLUSTER):
            host_id = f"host-{cluster_sequence:02d}-{host_index + 1:02d}"
            ip = f"192.18.{10 + cluster_sequence}.{10 + host_index + 1}"
            cpu_capacity = (32.0, 48.0, 64.0, 96.0)[host_index % 4]
            memory_capacity = {32.0: 128.0, 48.0: 192.0, 64.0: 256.0, 96.0: 384.0}[cpu_capacity]
            host_status, health_status = compute_host_runtime_state(cluster_sequence, host_index)
            hosts.append(
                {
                    "id": host_id,
                    "name": f"dbaas-{site['environment']}-worker-{cluster_sequence:02d}-{host_index + 1:02d}",
                    "ip": ip,
                    "clusterId": cluster["id"],
                    "hostStatus": host_status,
                    "healthStatus": health_status,
                    "cpuCapacity": cpu_capacity,
                    "memoryCapacity": memory_capacity,
                    "osName": "Alibaba Cloud Linux 3",
                    "kernelVersion": f"5.10.134-{cluster_sequence:02d}",
                    "arch": "x86_64",
                    "rack": f"rack-{site['region']}-{(host_index // 10) + 1:02d}",
                    "serialNumber": f"SN-{cluster_sequence:02d}-{host_index + 1:02d}-{site['environment'].upper()}",
                    "agentVersion": "2.7.3",
                    "disks": build_host_disks(host_id, host_index),
                }
            )
    return hosts


def build_host_disks(host_id: str, host_index: int) -> list[dict[str, Any]]:
    """Build per-host disk inventory."""

    base = float(110 + host_index * 3)
    disks = [
        {
            "diskId": f"{host_id}-disk-ssd-01",
            "name": "ssd-01",
            "type": "data",
            "mediaType": "SSD",
            "mountPoint": "/data-ssd",
            "capacity": 4096.0,
            "used": 1200.0 + host_index * 6.0,
            "healthStatus": "HEALTHY",
            "filesystem": "xfs",
            "raidType": "RAID10",
        },
        {
            "diskId": f"{host_id}-disk-hdd-01",
            "name": "hdd-01",
            "type": "log",
            "mediaType": "HDD",
            "mountPoint": "/data-hdd",
            "capacity": 12288.0,
            "used": 1800.0 + host_index * 8.0,
            "healthStatus": "HEALTHY",
            "filesystem": "ext4",
            "raidType": "RAID10",
        },
    ]
    return disks


def build_services(
    sites: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    hosts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build raw service-group seed data."""

    site_by_id = {site["id"]: site for site in sites}
    cluster_by_id = {cluster["id"]: cluster for cluster in clusters}
    global HOST_BY_ID
    host_by_id = {host["id"]: host for host in hosts}
    HOST_BY_ID = host_by_id
    host_ids_by_site: dict[str, list[str]] = {site["id"]: [] for site in sites}
    for host in hosts:
        site_id = cluster_by_id[host["clusterId"]]["siteId"]
        host_ids_by_site[site_id].append(host["id"])
    for host_ids in host_ids_by_site.values():
        host_ids.sort()

    container_counter = 0

    def next_container_ip() -> str:
        nonlocal container_counter
        third_octet = 10 + container_counter // 200
        fourth_octet = 11 + container_counter % 200
        container_counter += 1
        return f"192.168.{third_octet}.{fourth_octet}"

    def choose_host(site_id: str, key: str) -> dict[str, Any]:
        host_ids = host_ids_by_site[site_id]
        offset = sum(ord(char) for char in key) % len(host_ids)
        return host_by_id[host_ids[offset]]

    def choose_healthy_host(site_id: str, key: str) -> dict[str, Any]:
        eligible_host_ids = [
            host_id
            for host_id in host_ids_by_site[site_id]
            if host_by_id[host_id]["healthStatus"] == "HEALTHY"
            and host_by_id[host_id]["hostStatus"] == "RUNNING"
        ]
        offset = sum(ord(char) for char in key) % len(eligible_host_ids)
        return host_by_id[eligible_host_ids[offset]]

    services: list[dict[str, Any]] = []

    services.append(
        build_mysql_service(
            name="mysql-xf2",
            site=site_by_id["site-prod-sh-01"],
            user="payment-platform-team",
            subsystem="payment-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
            explicit_hosts={
                "mysql-primary-01": "host-01-01",
                "mysql-replica-01": "host-01-05",
                "proxy-01": "host-02-03",
                "proxy-02": "host-02-04",
                "sm-01": "host-02-05",
            },
        )
    )
    services.append(
        build_tidb_service(
            name="tidb-oltp",
            site=site_by_id["site-prod-sh-02"],
            user="db-platform-team",
            subsystem="tidb-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
            backup_type="snapshot",
            compress_mode="zstd",
        )
    )
    services.append(
        build_kafka_service(
            name="kafka-stream",
            site=site_by_id["site-prod-sh-01"],
            user="streaming-platform-team",
            subsystem="stream-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_influxdb_service(
            name="influxdb-monitor",
            site=site_by_id["site-prod-sh-01"],
            user="observability-platform-team",
            subsystem="monitor-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_redis_service(
            name="redis-cache",
            site=site_by_id["site-prod-sh-01"],
            user="cache-platform-team",
            subsystem="cache-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_mongodb_service(
            name="mongodb-docs",
            site=site_by_id["site-staging-sh-01"],
            user="content-platform-team",
            subsystem="content-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_elasticsearch_service(
            name="elasticsearch-search",
            site=site_by_id["site-staging-sh-01"],
            user="search-platform-team",
            subsystem="search-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_clickhouse_service(
            name="clickhouse-warehouse",
            site=site_by_id["site-prod-bj-01"],
            user="warehouse-platform-team",
            subsystem="warehouse-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )

    weighted_patterns = [pattern for pattern in SERVICE_PATTERNS for _ in range(pattern["weight"])]
    environments_to_sites: dict[str, list[dict[str, Any]]] = {}
    for site in sites:
        environments_to_sites.setdefault(site["environment"], []).append(site)

    for index in range(GENERATED_SERVICE_COUNT):
        pattern = weighted_patterns[index % len(weighted_patterns)]
        environment = pattern["environments"][index % len(pattern["environments"])]
        site = environments_to_sites[environment][index % len(environments_to_sites[environment])]
        serial = f"{index + 1:04d}"
        if pattern["type"] == "mysql":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_mysql_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("prod", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "tidb":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_tidb_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("prod", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "kafka":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_kafka_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("staging", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "influxdb":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_influxdb_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("prod", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "redis":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_redis_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("prod", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "mongodb":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_mongodb_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("staging", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "elasticsearch":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_elasticsearch_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("staging", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )
        elif pattern["type"] == "clickhouse":
            name = f"{pattern['name_prefix']}-{environment}-{site['region']}-{serial}"
            services.append(
                build_clickhouse_service(
                    name=name,
                    site=site,
                    user=pattern["user"].replace("prod", environment),
                    subsystem=pattern["subsystem"],
                    next_container_ip=next_container_ip,
                    choose_host=choose_host,
                    allow_anomalies=True,
                )
            )

    return services


def build_service_base(
    *,
    name: str,
    service_type: str,
    site: dict[str, Any],
    user: str,
    subsystem: str,
    architecture: str,
    sharding: bool,
    sequence_hint: int,
    backup_type: str = "logical",
    compress_mode: str = "gzip",
    cron_expression: str = "0 0 2 * * *",
) -> dict[str, Any]:
    """Create the common service-group shape."""

    third_octet = 10 + (int(site["sequence"]) * 16 + sequence_hint % 16)
    return {
        "name": name,
        "type": service_type,
        "user": user,
        "subsystem": subsystem,
        "siteId": site["id"],
        "architecture": architecture,
        "sharding": sharding,
        "healthStatus": "HEALTHY",
        "network": {
            "vpcId": f"vpc-{site['environment']}-{site['region']}",
            "subnetId": f"subnet-{site['id']}-{sequence_hint % 16:02d}",
            "cidr": f"192.168.{third_octet}.0/24",
            "gateway": f"192.168.{third_octet}.1",
        },
        "backupStrategy": {
            "enabled": True,
            "type": backup_type,
            "cronExpression": cron_expression,
            "retention": 7,
            "compressMode": compress_mode,
            "sendAlarm": True,
        },
        "services": [],
    }


def build_mysql_service(
    *,
    name: str,
    site: dict[str, Any],
    user: str,
    subsystem: str,
    next_container_ip,
    choose_host,
    allow_anomalies: bool,
    explicit_hosts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a MySQL service group."""

    service = build_service_base(
        name=name,
        service_type="mysql",
        site=site,
        user=user,
        subsystem=subsystem,
        architecture="proxy+switch-manager+mysql",
        sharding=False,
        sequence_hint=stable_index(name),
    )
    proxy_units = [
        make_unit(name=name, child_service_type="proxy", unit_id="proxy-01", role="proxy", image="proxy", version="1.0.0", cpu=2.0, memory=4.0, data_size=20.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, host_by_id=HOST_BY_ID, explicit_host_id=(explicit_hosts or {}).get("proxy-01"), allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="proxy", unit_id="proxy-02", role="proxy", image="proxy", version="1.0.0", cpu=2.0, memory=4.0, data_size=20.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, host_by_id=HOST_BY_ID, explicit_host_id=(explicit_hosts or {}).get("proxy-02"), allow_anomalies=allow_anomalies),
    ]
    sm_units = [
        make_unit(name=name, child_service_type="switch-manager", unit_id="sm-01", role="manager", image="switch-manager", version="1.0.0", cpu=1.0, memory=2.0, data_size=10.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, host_by_id=HOST_BY_ID, explicit_host_id=(explicit_hosts or {}).get("sm-01"), allow_anomalies=allow_anomalies),
    ]
    mysql_units = [
        make_unit(name=name, child_service_type="mysql", unit_id="mysql-primary-01", role="primary", image="mysql", version="8.0.36", cpu=8.0, memory=32.0, data_size=500.0, log_size=100.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, host_by_id=HOST_BY_ID, explicit_host_id=(explicit_hosts or {}).get("mysql-primary-01"), allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="mysql", unit_id="mysql-replica-01", role="replica", image="mysql", version="8.0.36", cpu=8.0, memory=32.0, data_size=500.0, log_size=100.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, host_by_id=HOST_BY_ID, explicit_host_id=(explicit_hosts or {}).get("mysql-replica-01"), allow_anomalies=allow_anomalies),
    ]
    service["services"] = [
        {"name": "proxy", "type": "proxy", "version": "1.0.0", "port": 3306, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": proxy_units},
        {"name": "switch-manager", "type": "switch-manager", "version": "1.0.0", "port": 8080, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": sm_units},
        {"name": "mysql", "type": "mysql", "version": "8.0.36", "port": 3306, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": mysql_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_tidb_service(
    *,
    name: str,
    site: dict[str, Any],
    user: str,
    subsystem: str,
    next_container_ip,
    choose_host,
    allow_anomalies: bool,
    backup_type: str = "snapshot",
    compress_mode: str = "zstd",
) -> dict[str, Any]:
    """Build a TiDB service group."""

    service = build_service_base(
        name=name,
        service_type="tidb",
        site=site,
        user=user,
        subsystem=subsystem,
        architecture="tidb+tikv+pd",
        sharding=False,
        sequence_hint=stable_index(name),
        backup_type=backup_type,
        compress_mode=compress_mode,
        cron_expression="0 0 1 * * *",
    )
    tidb_units = [
        make_unit(name=name, child_service_type="tidb", unit_id="tidb-01", role="server", image="pingcap/tidb", version="7.1.1", cpu=8.0, memory=32.0, data_size=80.0, log_size=40.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="tidb", unit_id="tidb-02", role="server", image="pingcap/tidb", version="7.1.1", cpu=8.0, memory=32.0, data_size=80.0, log_size=40.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
    ]
    tikv_units = [
        make_unit(name=name, child_service_type="tikv", unit_id="tikv-01", role="store", image="pingcap/tikv", version="7.1.1", cpu=16.0, memory=64.0, data_size=1200.0, log_size=120.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="tikv", unit_id="tikv-02", role="store", image="pingcap/tikv", version="7.1.1", cpu=16.0, memory=64.0, data_size=1200.0, log_size=120.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="tikv", unit_id="tikv-03", role="store", image="pingcap/tikv", version="7.1.1", cpu=16.0, memory=64.0, data_size=1200.0, log_size=120.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
    ]
    pd_units = [
        make_unit(name=name, child_service_type="pd", unit_id="pd-01", role="leader", image="pingcap/pd", version="7.1.1", cpu=4.0, memory=16.0, data_size=40.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="pd", unit_id="pd-02", role="follower", image="pingcap/pd", version="7.1.1", cpu=4.0, memory=16.0, data_size=40.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="pd", unit_id="pd-03", role="follower", image="pingcap/pd", version="7.1.1", cpu=4.0, memory=16.0, data_size=40.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
    ]
    service["services"] = [
        {"name": "tidb", "type": "tidb", "version": "7.1.1", "port": 4000, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": tidb_units},
        {"name": "tikv", "type": "tikv", "version": "7.1.1", "port": 20160, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": tikv_units},
        {"name": "pd", "type": "pd", "version": "7.1.1", "port": 2379, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": pd_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_kafka_service(*, name: str, site: dict[str, Any], user: str, subsystem: str, next_container_ip, choose_host, allow_anomalies: bool) -> dict[str, Any]:
    service = build_service_base(name=name, service_type="kafka", site=site, user=user, subsystem=subsystem, architecture="kafka+zookeeper", sharding=False, sequence_hint=stable_index(name))
    kafka_units = [
        make_unit(name=name, child_service_type="kafka", unit_id=f"kafka-0{i+1}", role="broker", image="bitnami/kafka", version="3.6.0", cpu=8.0, memory=16.0, data_size=600.0, log_size=80.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    zk_units = [
        make_unit(name=name, child_service_type="zookeeper", unit_id=f"zk-0{i+1}", role="observer" if i else "leader", image="bitnami/zookeeper", version="3.9.1", cpu=2.0, memory=8.0, data_size=80.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    service["services"] = [
        {"name": "kafka", "type": "kafka", "version": "3.6.0", "port": 9092, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": kafka_units},
        {"name": "zookeeper", "type": "zookeeper", "version": "3.9.1", "port": 2181, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": zk_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_influxdb_service(*, name: str, site: dict[str, Any], user: str, subsystem: str, next_container_ip, choose_host, allow_anomalies: bool) -> dict[str, Any]:
    service = build_service_base(name=name, service_type="influxdb", site=site, user=user, subsystem=subsystem, architecture="influxdb", sharding=False, sequence_hint=stable_index(name))
    units = [
        make_unit(name=name, child_service_type="influxdb", unit_id="influxdb-01", role="primary", image="influxdb", version="2.7.5", cpu=4.0, memory=8.0, data_size=240.0, log_size=40.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="influxdb", unit_id="influxdb-02", role="replica", image="influxdb", version="2.7.5", cpu=4.0, memory=8.0, data_size=240.0, log_size=40.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
    ]
    service["services"] = [
        {"name": "influxdb", "type": "influxdb", "version": "2.7.5", "port": 8086, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": units}
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_redis_service(*, name: str, site: dict[str, Any], user: str, subsystem: str, next_container_ip, choose_host, allow_anomalies: bool) -> dict[str, Any]:
    service = build_service_base(name=name, service_type="redis", site=site, user=user, subsystem=subsystem, architecture="redis+sentinel", sharding=False, sequence_hint=stable_index(name))
    redis_units = [
        make_unit(name=name, child_service_type="redis", unit_id="redis-primary-01", role="primary", image="redis", version="7.2.4", cpu=4.0, memory=16.0, data_size=120.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="redis", unit_id="redis-replica-01", role="replica", image="redis", version="7.2.4", cpu=4.0, memory=16.0, data_size=120.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="redis", unit_id="redis-replica-02", role="replica", image="redis", version="7.2.4", cpu=4.0, memory=16.0, data_size=120.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
    ]
    sentinel_units = [
        make_unit(name=name, child_service_type="sentinel", unit_id="sentinel-01", role="leader", image="redis-sentinel", version="7.2.4", cpu=1.0, memory=4.0, data_size=10.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="sentinel", unit_id="sentinel-02", role="follower", image="redis-sentinel", version="7.2.4", cpu=1.0, memory=4.0, data_size=10.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
        make_unit(name=name, child_service_type="sentinel", unit_id="sentinel-03", role="follower", image="redis-sentinel", version="7.2.4", cpu=1.0, memory=4.0, data_size=10.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies),
    ]
    service["services"] = [
        {"name": "redis", "type": "redis", "version": "7.2.4", "port": 6379, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": redis_units},
        {"name": "sentinel", "type": "sentinel", "version": "7.2.4", "port": 26379, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": sentinel_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_mongodb_service(*, name: str, site: dict[str, Any], user: str, subsystem: str, next_container_ip, choose_host, allow_anomalies: bool) -> dict[str, Any]:
    service = build_service_base(name=name, service_type="mongodb", site=site, user=user, subsystem=subsystem, architecture="mongos+configsvr+shard", sharding=True, sequence_hint=stable_index(name))
    mongos_units = [
        make_unit(name=name, child_service_type="mongos", unit_id=f"mongos-0{i+1}", role="router", image="mongodb/mongos", version="7.0.9", cpu=4.0, memory=8.0, data_size=20.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(2)
    ]
    config_units = [
        make_unit(name=name, child_service_type="configsvr", unit_id=f"configsvr-0{i+1}", role="config", image="mongodb/configsvr", version="7.0.9", cpu=4.0, memory=16.0, data_size=80.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    shard_units = [
        make_unit(name=name, child_service_type="shard", unit_id=f"shard-0{i+1}", role="replica" if i else "primary", image="mongodb/shard", version="7.0.9", cpu=8.0, memory=32.0, data_size=900.0, log_size=80.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    service["services"] = [
        {"name": "mongos", "type": "mongos", "version": "7.0.9", "port": 27017, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": mongos_units},
        {"name": "configsvr", "type": "configsvr", "version": "7.0.9", "port": 27019, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": config_units},
        {"name": "shard", "type": "shard", "version": "7.0.9", "port": 27018, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": shard_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_elasticsearch_service(*, name: str, site: dict[str, Any], user: str, subsystem: str, next_container_ip, choose_host, allow_anomalies: bool) -> dict[str, Any]:
    service = build_service_base(name=name, service_type="elasticsearch", site=site, user=user, subsystem=subsystem, architecture="elasticsearch+kibana", sharding=False, sequence_hint=stable_index(name))
    es_units = [
        make_unit(name=name, child_service_type="elasticsearch", unit_id=f"es-0{i+1}", role="data" if i else "master", image="elasticsearch", version="8.13.4", cpu=8.0, memory=32.0, data_size=700.0, log_size=60.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    kibana_units = [
        make_unit(name=name, child_service_type="kibana", unit_id="kibana-01", role="ui", image="kibana", version="8.13.4", cpu=2.0, memory=4.0, data_size=20.0, log_size=10.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
    ]
    service["services"] = [
        {"name": "elasticsearch", "type": "elasticsearch", "version": "8.13.4", "port": 9200, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": es_units},
        {"name": "kibana", "type": "kibana", "version": "8.13.4", "port": 5601, "healthStatus": "HEALTHY", "clusterHA": False, "nodeHA": True, "platformAuto": None, "units": kibana_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def build_clickhouse_service(*, name: str, site: dict[str, Any], user: str, subsystem: str, next_container_ip, choose_host, allow_anomalies: bool) -> dict[str, Any]:
    service = build_service_base(name=name, service_type="clickhouse", site=site, user=user, subsystem=subsystem, architecture="clickhouse+keeper", sharding=False, sequence_hint=stable_index(name))
    clickhouse_units = [
        make_unit(name=name, child_service_type="clickhouse", unit_id=f"clickhouse-0{i+1}", role="replica" if i else "primary", image="clickhouse/clickhouse-server", version="24.4.1", cpu=16.0, memory=64.0, data_size=1400.0, log_size=100.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    keeper_units = [
        make_unit(name=name, child_service_type="keeper", unit_id=f"keeper-0{i+1}", role="leader" if i == 0 else "follower", image="clickhouse/clickhouse-keeper", version="24.4.1", cpu=2.0, memory=8.0, data_size=40.0, log_size=20.0, site_id=site["id"], next_container_ip=next_container_ip, choose_host=choose_host, allow_anomalies=allow_anomalies)
        for i in range(3)
    ]
    service["services"] = [
        {"name": "clickhouse", "type": "clickhouse", "version": "24.4.1", "port": 9000, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": clickhouse_units},
        {"name": "keeper", "type": "keeper", "version": "24.4.1", "port": 9181, "healthStatus": "HEALTHY", "clusterHA": True, "nodeHA": True, "platformAuto": None, "units": keeper_units},
    ]
    return apply_runtime_health(service, allow_anomalies=allow_anomalies)


def make_unit(
    *,
    name: str,
    child_service_type: str,
    unit_id: str,
    role: str,
    image: str,
    version: str,
    cpu: float,
    memory: float,
    data_size: float,
    log_size: float,
    site_id: str,
    next_container_ip,
    choose_host,
    host_by_id: dict[str, dict[str, Any]] | None = None,
    explicit_host_id: str | None = None,
    allow_anomalies: bool = True,
) -> dict[str, Any]:
    """Create a unit bound to a host and disks."""

    key = f"{name}:{child_service_type}:{unit_id}"
    host = host_by_id[explicit_host_id] if explicit_host_id is not None else choose_host(site_id, key)
    unit_health_status, container_status = compute_unit_runtime_state(
        service_name=name,
        child_service_type=child_service_type,
        unit_id=unit_id,
        host=host,
        allow_anomalies=allow_anomalies,
    )

    data_disk = pick_host_disk(host, disk_types={"data"}, preferred_media=data_media_preference(child_service_type))
    log_disk = pick_host_disk(host, disk_types={"log", "data"}, preferred_media=log_media_preference(child_service_type))

    return {
        "id": unit_id,
        "name": unit_id,
        "type": "docker",
        "role": role,
        "image": image,
        "version": version,
        "healthStatus": unit_health_status,
        "containerStatus": container_status,
        "hostId": host["id"],
        "containerIp": next_container_ip(),
        "cpu": cpu,
        "memory": memory,
        "storage": {
            "data": {
                "diskId": data_disk["diskId"],
                "mountPoint": f"/dbaas/{child_service_type}/{unit_id}/data",
                "size": data_size,
            },
            "log": {
                "diskId": log_disk["diskId"],
                "mountPoint": f"/dbaas/{child_service_type}/{unit_id}/log",
                "size": log_size,
            },
        },
    }


def compute_host_runtime_state(cluster_sequence: int, host_index: int) -> tuple[str, str]:
    """Compute host runtime state with deterministic anomalies."""

    # Keep the early hosts in the first clusters healthy for stable anchor examples.
    if cluster_sequence <= 2 and host_index < 8:
        return "RUNNING", "HEALTHY"

    score = (cluster_sequence * 37 + host_index * 17) % 100
    if score < 3:
        return "FAILED", "UNHEALTHY"
    if score < 8:
        return "MAINTENANCE", "WARN"
    if score < 18:
        return "RUNNING", "WARN"
    return "RUNNING", "HEALTHY"


def compute_unit_runtime_state(
    *,
    service_name: str,
    child_service_type: str,
    unit_id: str,
    host: dict[str, Any],
    allow_anomalies: bool,
) -> tuple[str, str]:
    """Compute unit health/container status with deterministic anomalies."""

    if not allow_anomalies:
        return "HEALTHY", "RUNNING"

    host_status = host["hostStatus"]
    host_health = host["healthStatus"]
    if host_status == "FAILED":
        return "UNHEALTHY", "FAILED"
    if host_status == "MAINTENANCE":
        return "WARN", "STOPPED"
    if host_health == "WARN":
        return "WARN", "RUNNING"

    score = stable_index(f"{service_name}:{child_service_type}:{unit_id}") % 100
    if score < 2:
        return "UNHEALTHY", "FAILED"
    if score < 6:
        return "WARN", "RESTARTING"
    if score < 14:
        return "WARN", "RUNNING"
    return "HEALTHY", "RUNNING"


def derive_health_status(statuses: list[str]) -> str:
    """Aggregate a list of health states."""

    unhealthy_count = sum(1 for status in statuses if status == "UNHEALTHY")
    warn_count = sum(1 for status in statuses if status == "WARN")
    total = len(statuses)
    if unhealthy_count == 0 and warn_count == 0:
        return "HEALTHY"
    if unhealthy_count * 2 >= total:
        return "UNHEALTHY"
    return "WARN"


def apply_runtime_health(service: dict[str, Any], *, allow_anomalies: bool) -> dict[str, Any]:
    """Derive child-service and service-group health from unit states."""

    if not allow_anomalies:
        for child_service in service["services"]:
            child_service["healthStatus"] = "HEALTHY"
            for unit in child_service["units"]:
                unit["healthStatus"] = "HEALTHY"
                unit["containerStatus"] = "RUNNING"
        service["healthStatus"] = "HEALTHY"
        return service

    child_healths: list[str] = []
    for child_service in service["services"]:
        unit_healths = [unit["healthStatus"] for unit in child_service["units"]]
        child_service["healthStatus"] = derive_health_status(unit_healths)
        child_healths.append(child_service["healthStatus"])

    service["healthStatus"] = derive_health_status(child_healths)
    return service


def pick_host_disk(host: dict[str, Any], *, disk_types: set[str], preferred_media: tuple[str, ...]) -> dict[str, Any]:
    """Pick a disk on the host for a volume."""

    candidates = [disk for disk in host["disks"] if disk["type"] in disk_types]
    for media_type in preferred_media:
        for disk in candidates:
            if disk["mediaType"] == media_type:
                return disk
    return candidates[0]


def data_media_preference(child_service_type: str) -> tuple[str, ...]:
    if child_service_type in {"mysql", "tidb", "tikv", "clickhouse", "elasticsearch", "mongodb", "configsvr", "shard"}:
        return ("SSD", "HDD")
    return ("SSD", "HDD")


def log_media_preference(child_service_type: str) -> tuple[str, ...]:
    if child_service_type in {"mysql", "tidb", "tikv", "clickhouse"}:
        return ("HDD", "SSD")
    return ("HDD", "SSD")


def stable_index(value: str) -> int:
    """Return a deterministic integer index for a string."""

    return sum(ord(char) for char in value)


def build_backups(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build backup seed data with both curated and high-volume generic samples."""

    services_by_name = {service["name"]: service for service in services}
    backups: list[dict[str, Any]] = []

    backups.extend(build_mysql_xf2_backups(services_by_name["mysql-xf2"]))
    backups.extend(build_tidb_oltp_backups(services_by_name["tidb-oltp"]))
    backups.extend(build_redis_cache_backups(services_by_name["redis-cache"]))

    for service_index, service in enumerate(services):
        if service["name"] in SPECIAL_BACKUP_SERVICE_NAMES:
            continue
        backups.extend(build_generic_service_backups(service, service_index=service_index))

    return backups


def build_mysql_xf2_backups(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Build curated MySQL backups used by real-agent backup questions."""

    targets = {
        target["unit_name"]: target
        for target in build_backup_targets(service)
    }
    return [
        make_backup_record(
            service=service,
            target=targets["mysql-primary-01"],
            backup_id="backup-mysql-xf2-001",
            backup_type="full",
            started_at=datetime(2026, 6, 1, 7, 30, 6),
            finished_at=datetime(2026, 6, 1, 7, 30, 10),
            expires_at=datetime(2026, 6, 8, 7, 30, 10),
            duration_seconds=4,
            size_bytes=6 * 1024 * 1024 + 187_321,
            storage_type="NAS",
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="自动备份",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-replica-01"],
            backup_id="backup-mysql-xf2-002",
            backup_type="table",
            started_at=datetime(2026, 5, 30, 3, 0, 0),
            finished_at=datetime(2026, 5, 30, 3, 5, 0),
            expires_at=datetime(2026, 5, 31, 3, 5, 0),
            duration_seconds=300,
            size_bytes=3 * 1024 * 1024 + 641_024,
            storage_type="S3",
            compress_mode="none",
            task_status="succeeded",
            task_error=None,
            valid_status="unchecked",
            remark="已过期但未删除",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-primary-01"],
            backup_id="backup-mysql-xf2-003",
            backup_type="full",
            started_at=datetime(2026, 5, 27, 2, 0, 0),
            finished_at=datetime(2026, 5, 27, 2, 2, 0),
            expires_at=datetime(2026, 6, 3, 2, 2, 0),
            duration_seconds=120,
            size_bytes=5 * 1024 * 1024 + 913_408,
            storage_type="NAS",
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="自动备份",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-primary-01"],
            backup_id="backup-mysql-xf2-004",
            backup_type="incremental",
            started_at=datetime(2026, 5, 29, 2, 0, 0),
            finished_at=datetime(2026, 5, 29, 2, 10, 0),
            expires_at=datetime(2026, 6, 5, 2, 10, 0),
            duration_seconds=600,
            size_bytes=2 * 1024 * 1024 + 438_272,
            storage_type="S3",
            compress_mode="gzip",
            task_status="failed",
            task_error="xtrabackup timeout",
            valid_status=None,
            remark="增量备份失败",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-primary-01"],
            backup_id="backup-mysql-xf2-005",
            backup_type="full",
            started_at=datetime(2026, 5, 31, 21, 30, 0),
            finished_at=datetime(2026, 5, 31, 21, 33, 0),
            expires_at=datetime(2026, 6, 7, 21, 33, 0),
            duration_seconds=180,
            size_bytes=6 * 1024 * 1024 + 812_544,
            storage_type="NAS",
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="例行全量备份",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-replica-01"],
            backup_id="backup-mysql-xf2-006",
            backup_type="incremental",
            started_at=datetime(2026, 6, 1, 1, 0, 0),
            finished_at=datetime(2026, 6, 1, 1, 3, 0),
            expires_at=datetime(2026, 6, 8, 1, 3, 0),
            duration_seconds=180,
            size_bytes=2 * 1024 * 1024 + 214_016,
            storage_type="S3",
            compress_mode="gzip",
            task_status="failed",
            task_error="S3 upload failed",
            valid_status=None,
            remark="备份上传失败",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-primary-01"],
            backup_id="backup-mysql-xf2-007",
            backup_type="full",
            started_at=datetime(2026, 5, 24, 1, 0, 0),
            finished_at=datetime(2026, 5, 24, 1, 2, 30),
            expires_at=datetime(2026, 5, 31, 1, 2, 30),
            duration_seconds=150,
            size_bytes=5 * 1024 * 1024 + 221_184,
            storage_type="NAS",
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="周常备份",
        ),
        make_backup_record(
            service=service,
            target=targets["mysql-primary-01"],
            backup_id="backup-mysql-xf2-008",
            backup_type="full",
            started_at=datetime(2026, 5, 18, 2, 0, 0),
            finished_at=datetime(2026, 5, 18, 2, 2, 0),
            expires_at=datetime(2026, 5, 25, 2, 2, 0),
            duration_seconds=120,
            size_bytes=5 * 1024 * 1024 + 114_688,
            storage_type=None,
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="已删除备份",
            deleted=True,
        ),
    ]


def build_tidb_oltp_backups(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Build curated TiDB backups covering running and failed states."""

    targets = {
        target["unit_name"]: target
        for target in build_backup_targets(service)
    }
    return [
        make_backup_record(
            service=service,
            target=targets["tikv-01"],
            backup_id="backup-tidb-oltp-001",
            backup_type="incremental",
            started_at=datetime(2026, 6, 1, 9, 30, 0),
            finished_at=None,
            expires_at=datetime(2026, 6, 10, 9, 30, 0),
            duration_seconds=None,
            size_bytes=48 * 1024 * 1024 + 1_048_576,
            storage_type="S3",
            compress_mode="gzip",
            task_status="running",
            task_error=None,
            valid_status=None,
            remark="增量备份执行中",
        ),
        make_backup_record(
            service=service,
            target=targets["tidb-01"],
            backup_id="backup-tidb-oltp-002",
            backup_type="full",
            started_at=datetime(2026, 5, 31, 1, 20, 0),
            finished_at=datetime(2026, 5, 31, 1, 35, 0),
            expires_at=datetime(2026, 6, 7, 1, 35, 0),
            duration_seconds=900,
            size_bytes=64 * 1024 * 1024 + 2_097_152,
            storage_type="S3",
            compress_mode="zstd",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="TiDB 全量快照",
        ),
        make_backup_record(
            service=service,
            target=targets["tikv-02"],
            backup_id="backup-tidb-oltp-003",
            backup_type="incremental",
            started_at=datetime(2026, 5, 28, 1, 10, 0),
            finished_at=datetime(2026, 5, 28, 1, 18, 30),
            expires_at=datetime(2026, 6, 4, 1, 18, 30),
            duration_seconds=510,
            size_bytes=22 * 1024 * 1024 + 524_288,
            storage_type="S3",
            compress_mode="gzip",
            task_status="failed",
            task_error="tikv snapshot checksum mismatch",
            valid_status=None,
            remark="备份校验失败",
        ),
        make_backup_record(
            service=service,
            target=targets["tikv-03"],
            backup_id="backup-tidb-oltp-004",
            backup_type="snapshot",
            started_at=datetime(2026, 5, 23, 0, 50, 0),
            finished_at=datetime(2026, 5, 23, 1, 5, 0),
            expires_at=datetime(2026, 5, 30, 1, 5, 0),
            duration_seconds=900,
            size_bytes=58 * 1024 * 1024 + 786_432,
            storage_type="NAS",
            compress_mode=None,
            task_status="succeeded",
            task_error=None,
            valid_status="unchecked",
            remark="已过期但未删除",
        ),
        make_backup_record(
            service=service,
            target=targets["tidb-02"],
            backup_id="backup-tidb-oltp-005",
            backup_type="full",
            started_at=datetime(2026, 5, 15, 1, 0, 0),
            finished_at=datetime(2026, 5, 15, 1, 16, 0),
            expires_at=datetime(2026, 5, 22, 1, 16, 0),
            duration_seconds=960,
            size_bytes=60 * 1024 * 1024 + 131_072,
            storage_type="NAS",
            compress_mode="zstd",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="已删除备份",
            deleted=True,
        ),
    ]


def build_redis_cache_backups(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Build curated Redis backups with mixed statuses."""

    targets = {
        target["unit_name"]: target
        for target in build_backup_targets(service)
    }
    return [
        make_backup_record(
            service=service,
            target=targets["redis-primary-01"],
            backup_id="backup-redis-cache-001",
            backup_type="snapshot",
            started_at=datetime(2026, 6, 1, 1, 0, 0),
            finished_at=datetime(2026, 6, 1, 1, 0, 20),
            expires_at=datetime(2026, 6, 3, 1, 0, 20),
            duration_seconds=20,
            size_bytes=8 * 1024 * 1024 + 97_152,
            storage_type="NAS",
            compress_mode="zstd",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="缓存快照",
        ),
        make_backup_record(
            service=service,
            target=targets["redis-replica-01"],
            backup_id="backup-redis-cache-002",
            backup_type="snapshot",
            started_at=datetime(2026, 5, 29, 1, 20, 0),
            finished_at=datetime(2026, 5, 29, 1, 20, 45),
            expires_at=datetime(2026, 6, 1, 1, 20, 45),
            duration_seconds=45,
            size_bytes=4 * 1024 * 1024 + 401_408,
            storage_type="S3",
            compress_mode="zstd",
            task_status="failed",
            task_error="replica link broken",
            valid_status=None,
            remark="从库快照失败",
        ),
        make_backup_record(
            service=service,
            target=targets["redis-replica-02"],
            backup_id="backup-redis-cache-003",
            backup_type="full",
            started_at=datetime(2026, 5, 22, 0, 30, 0),
            finished_at=datetime(2026, 5, 22, 0, 30, 25),
            expires_at=datetime(2026, 5, 29, 0, 30, 25),
            duration_seconds=25,
            size_bytes=3 * 1024 * 1024 + 225_280,
            storage_type=None,
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="unchecked",
            remark="已过期但未删除",
        ),
        make_backup_record(
            service=service,
            target=targets["redis-primary-01"],
            backup_id="backup-redis-cache-deleted",
            backup_type="full",
            started_at=datetime(2026, 5, 20, 0, 0, 0),
            finished_at=datetime(2026, 5, 20, 0, 0, 12),
            expires_at=datetime(2026, 5, 27, 0, 0, 12),
            duration_seconds=12,
            size_bytes=6 * 1024 * 1024,
            storage_type="NAS",
            compress_mode="gzip",
            task_status="succeeded",
            task_error=None,
            valid_status="valid",
            remark="已删除备份",
            deleted=True,
        ),
    ]


def build_generic_service_backups(service: dict[str, Any], *, service_index: int) -> list[dict[str, Any]]:
    """Build deterministic generic backups for high-volume seed coverage."""

    targets = build_backup_targets(service)
    backup_types = backup_type_cycle(service["type"])
    compress_mode = service.get("backupStrategy", {}).get("compressMode", "gzip")
    visible_storage_cycle = ("NAS", "S3", None)
    second_status_cycle = ("succeeded", "failed", "timeout", "canceled")
    owner_user = service["user"]

    recent_target = targets[service_index % len(targets)]
    second_target = targets[(service_index + 1) % len(targets)]
    third_target = targets[(service_index + 2) % len(targets)]

    recent_started = BACKUP_REFERENCE_TIME - timedelta(
        days=service_index % 3,
        hours=2 + (service_index % 16),
        minutes=(service_index * 7) % 60,
    )
    recent_status = "running" if service_index % 17 == 0 else "succeeded"
    recent_size = estimated_backup_size_bytes(service["type"], service_index, backup_types[0], variant=0)
    records = [
        make_backup_record(
            service=service,
            target=recent_target,
            backup_id=f"backup-{service['name']}-recent-{service_index:04d}",
            backup_type=backup_types[0],
            started_at=recent_started,
            finished_at=None if recent_status == "running" else recent_started + timedelta(seconds=recent_duration_seconds(recent_size)),
            expires_at=recent_started + timedelta(days=7 + service_index % 5, seconds=recent_duration_seconds(recent_size)),
            duration_seconds=None if recent_status == "running" else recent_duration_seconds(recent_size),
            size_bytes=recent_size,
            storage_type=visible_storage_cycle[service_index % len(visible_storage_cycle)],
            compress_mode=compress_mode,
            task_status=recent_status,
            task_error=None,
            valid_status=None if recent_status == "running" else "valid",
            remark="备份执行中" if recent_status == "running" else "例行备份",
        )
    ]

    second_status = second_status_cycle[(service_index // 3) % len(second_status_cycle)]
    second_started = BACKUP_REFERENCE_TIME - timedelta(
        days=4 + service_index % 6,
        hours=1 + (service_index * 3) % 20,
        minutes=(service_index * 11) % 60,
    )
    second_size = estimated_backup_size_bytes(service["type"], service_index, backup_types[1], variant=1)
    second_duration = recent_duration_seconds(second_size) + 60
    records.append(
        make_backup_record(
            service=service,
            target=second_target,
            backup_id=f"backup-{service['name']}-weekly-{service_index:04d}",
            backup_type=backup_types[1],
            started_at=second_started,
            finished_at=second_started + timedelta(seconds=second_duration),
            expires_at=second_started + timedelta(days=7, seconds=second_duration),
            duration_seconds=second_duration,
            size_bytes=second_size,
            storage_type=visible_storage_cycle[(service_index + 1) % len(visible_storage_cycle)],
            compress_mode="none" if service_index % 5 == 0 else compress_mode,
            task_status=second_status,
            task_error=task_error_for_status(second_status, service["type"]),
            valid_status=valid_status_for_status(second_status, service_index),
            remark=remark_for_status(second_status),
        )
    )

    deleted = service_index % 6 == 0
    old_started = BACKUP_REFERENCE_TIME - timedelta(
        days=15 + service_index % 18,
        hours=1 + (service_index * 5) % 18,
        minutes=(service_index * 13) % 60,
    )
    old_size = estimated_backup_size_bytes(service["type"], service_index, backup_types[2], variant=2)
    old_duration = recent_duration_seconds(old_size) + 120
    records.append(
        make_backup_record(
            service=service,
            target=third_target,
            backup_id=f"backup-{service['name']}-archived-{service_index:04d}",
            backup_type=backup_types[2],
            started_at=old_started,
            finished_at=old_started + timedelta(seconds=old_duration),
            expires_at=old_started + timedelta(days=3 + service_index % 4, seconds=old_duration),
            duration_seconds=old_duration,
            size_bytes=old_size,
            storage_type=visible_storage_cycle[(service_index + 2) % len(visible_storage_cycle)],
            compress_mode=None if service_index % 8 == 0 else compress_mode,
            task_status="succeeded",
            task_error=None,
            valid_status="unchecked" if service_index % 4 == 0 else "valid",
            remark="已删除备份" if deleted else "已过期但未删除",
            deleted=deleted,
            owner_user=owner_user,
        )
    )

    return records


def build_backup_targets(service: dict[str, Any]) -> list[dict[str, str]]:
    """Pick backup-capable child services and units for a service."""

    preferred_types = backup_capable_child_types(service["type"])
    targets: list[dict[str, str]] = []
    for child_service in service["services"]:
        child_type = child_service["type"]
        if preferred_types and child_type not in preferred_types:
            continue
        for unit_index, unit in enumerate(child_service["units"], start=1):
            targets.append(
                {
                    "child_service_name": derive_child_service_name(child_type, unit["name"], unit_index),
                    "child_service_type": child_type,
                    "unit_name": unit["name"],
                }
            )

    if targets:
        return targets

    first_child = service["services"][0]
    first_unit = first_child["units"][0]
    return [
        {
            "child_service_name": derive_child_service_name(first_child["type"], first_unit["name"], 1),
            "child_service_type": first_child["type"],
            "unit_name": first_unit["name"],
        }
    ]


def backup_capable_child_types(service_type: str) -> set[str]:
    """Return the child-service types that typically produce backup files."""

    mapping = {
        "mysql": {"mysql"},
        "tidb": {"tidb", "tikv"},
        "kafka": {"kafka"},
        "influxdb": {"influxdb"},
        "redis": {"redis"},
        "mongodb": {"shard"},
        "elasticsearch": {"elasticsearch"},
        "clickhouse": {"clickhouse"},
    }
    return mapping.get(service_type, set())


def derive_child_service_name(child_service_type: str, unit_name: str, unit_index: int) -> str:
    """Derive a model-friendly child_service_name from unit naming."""

    if unit_name.endswith("-01") or unit_name.endswith("-02") or unit_name.endswith("-03"):
        prefix, _separator, suffix = unit_name.rpartition("-")
        if suffix.isdigit() and prefix:
            if child_service_type in {"tikv", "shard", "clickhouse", "elasticsearch", "kafka"}:
                return f"{child_service_type}-shard-{int(suffix):02d}"
            return prefix
    return f"{child_service_type}-{unit_index:02d}"


def backup_type_cycle(service_type: str) -> tuple[str, str, str]:
    """Return a deterministic backup_type cycle by service type."""

    mapping = {
        "mysql": ("full", "incremental", "table"),
        "tidb": ("snapshot", "incremental", "full"),
        "kafka": ("full", "incremental", "full"),
        "influxdb": ("full", "incremental", "full"),
        "redis": ("snapshot", "snapshot", "full"),
        "mongodb": ("full", "incremental", "table"),
        "elasticsearch": ("snapshot", "incremental", "snapshot"),
        "clickhouse": ("full", "incremental", "table"),
    }
    return mapping.get(service_type, ("full", "incremental", "full"))


def estimated_backup_size_bytes(service_type: str, service_index: int, backup_type: str, *, variant: int) -> int:
    """Estimate a deterministic MB-scale backup size."""

    base_mb = {
        "mysql": 6.0,
        "tidb": 48.0,
        "kafka": 18.0,
        "influxdb": 8.0,
        "redis": 3.0,
        "mongodb": 24.0,
        "elasticsearch": 30.0,
        "clickhouse": 54.0,
    }.get(service_type, 10.0)
    multiplier = {
        "full": 1.0,
        "snapshot": 1.0,
        "incremental": 0.42,
        "table": 0.18,
    }.get(backup_type, 1.0)
    size_mb = (base_mb + (service_index % 7) * 0.65 + variant * 0.45) * multiplier
    return int(size_mb * 1024 * 1024)


def recent_duration_seconds(size_bytes: int) -> int:
    """Derive deterministic duration from size bytes."""

    return max(15, size_bytes // (256 * 1024))


def task_error_for_status(task_status: str, service_type: str) -> str | None:
    """Return a representative task_error for non-success terminal states."""

    if task_status == "failed":
        return {
            "mysql": "xtrabackup timeout",
            "tidb": "snapshot upload failed",
            "redis": "rdb export failed",
            "kafka": "broker leader changed",
            "influxdb": "retention shard locked",
            "mongodb": "mongodump checksum mismatch",
            "elasticsearch": "repository verify failed",
            "clickhouse": "freeze partition failed",
        }.get(service_type, "backup task failed")
    if task_status == "timeout":
        return "backup task timeout"
    if task_status == "canceled":
        return "backup task canceled by operator"
    return None


def valid_status_for_status(task_status: str, service_index: int) -> str | None:
    """Return valid_status only when it makes semantic sense."""

    if task_status == "succeeded":
        return "unchecked" if service_index % 4 == 0 else "valid"
    return None


def remark_for_status(task_status: str) -> str:
    """Return a concise human-readable remark."""

    if task_status == "failed":
        return "备份失败"
    if task_status == "timeout":
        return "备份超时"
    if task_status == "canceled":
        return "备份已取消"
    return "周常备份"


def make_backup_record(
    *,
    service: dict[str, Any],
    target: dict[str, str],
    backup_id: str,
    backup_type: str,
    started_at: datetime,
    finished_at: datetime | None,
    expires_at: datetime | None,
    duration_seconds: int | None,
    size_bytes: int,
    storage_type: str | None,
    compress_mode: str | None,
    task_status: str,
    task_error: str | None,
    valid_status: str | None,
    remark: str,
    deleted: bool = False,
    owner_user: str | None = None,
) -> dict[str, Any]:
    """Build a single backup seed record."""

    actual_owner_user = owner_user or service["user"]
    return {
        "backup_id": backup_id,
        "task_id": f"task-{backup_id}",
        "service_name": service["name"],
        "service_type": service["type"],
        "child_service_name": target["child_service_name"],
        "child_service_type": target["child_service_type"],
        "unit_name": target["unit_name"],
        "backup_type": backup_type,
        "backup_path": build_backup_path(
            owner_user=actual_owner_user,
            service_name=service["name"],
            backup_type=backup_type,
            unit_name=target["unit_name"],
            started_at=started_at,
        ),
        "size_bytes": size_bytes,
        "storage_type": storage_type,
        "compress_mode": compress_mode,
        "started_at": format_backup_datetime(started_at),
        "finished_at": format_backup_datetime(finished_at),
        "expires_at": format_backup_datetime(expires_at),
        "duration_seconds": duration_seconds,
        "task_status": task_status,
        "task_error": task_error,
        "valid_status": valid_status,
        "remark": remark,
        "owner_user": actual_owner_user,
        "deleted": deleted,
    }


def build_backup_path(
    *,
    owner_user: str,
    service_name: str,
    backup_type: str,
    unit_name: str,
    started_at: datetime,
) -> str:
    """Build a deterministic backup path."""

    return (
        f"/BACKUP/{owner_user}/{service_name}/{backup_type}/"
        f"{unit_name}/{started_at.strftime('%Y%m%dT%H%M%S')}"
    )


def format_backup_datetime(value: datetime | None) -> str | None:
    """Format backup timestamps as local datetime strings."""

    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def write_json(path: Path, payload: list[dict[str, Any]]) -> None:
    """Write JSON data with stable formatting."""

    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

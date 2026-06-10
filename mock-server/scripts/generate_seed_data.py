"""Generate normalized DBaaS seed data for the mock server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
import secrets
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RNG = random.Random(20260421)
HOST_BY_ID: dict[str, dict[str, Any]] = {}
UNIT_IDS: set[str] = set()
SITE_IDS: set[str] = set()
HOST_IDS: set[str] = set()
CLUSTER_IDS: set[str] = set()
AREA_IDS: set[str] = set()
BACKUP_IDS: set[str] = set()
TASK_IDS: set[str] = set()

SITE_COUNT = 12
CLUSTERS_PER_SITE = 4
HOSTS_PER_CLUSTER = 60
GENERATED_SERVICE_COUNT = 2200
BACKUP_REFERENCE_TIME = datetime(2026, 6, 1, 10, 0, 0)
MYSQL_ANCHOR_SERVICE_NAME = "payad001"
TIDB_ANCHOR_SERVICE_NAME = "ordad002"
REDIS_ANCHOR_SERVICE_NAME = "sesad003"
SPECIAL_BACKUP_SERVICE_NAMES = {
    MYSQL_ANCHOR_SERVICE_NAME,
    TIDB_ANCHOR_SERVICE_NAME,
    REDIS_ANCHOR_SERVICE_NAME,
}

ARCHITECTURES = (("amd64", "X86", "1"), ("arm64", "ARM", "2"))
OWNER_NAMES = ("陈思远", "李明哲", "王佳宁", "赵雨晴", "周启航", "吴嘉怡", "郑皓文", "孙亦辰", "马婧雯", "胡承泽")
BUSINESS_CATALOG: dict[str, tuple[str, str, str]] = {
    "account": ("ACC", "账户中心系统ZHZT", "账户资料库"),
    "analytics": ("ANA", "经营分析系统JYFX", "指标明细库"),
    "billing": ("BIL", "计费账务系统JFZW", "账单结算库"),
    "cache": ("CAC", "缓存加速系统HCJS", "热点缓存库"),
    "content": ("CNT", "内容管理系统NRGL", "内容元数据库"),
    "growth": ("GRW", "增长运营系统ZZYY", "活动投放库"),
    "inventory": ("INV", "库存履约系统KCLY", "库存流水库"),
    "messaging": ("MSG", "消息通知系统XXTZ", "消息投递库"),
    "monitor": ("MON", "监控观测系统JKGC", "时序监控库"),
    "order": ("ORD", "交易订单系统JYDD", "订单核心库"),
    "payment": ("PAY", "支付核心系统ZFHX", "支付交易库"),
    "payment-platform": ("PAY", "支付核心系统ZFHX", "支付路由库"),
    "profile": ("PRF", "客户画像系统KHHX", "画像标签库"),
    "recommend": ("REC", "智能推荐系统ZNTJ", "推荐特征库"),
    "search": ("SEA", "搜索检索系统SSJS", "搜索索引库"),
    "session": ("SES", "会话缓存系统HHHC", "登录态缓存库"),
    "stream": ("STM", "实时流处理系统SSLC", "流式消息库"),
    "stream-platform": ("STM", "实时流处理系统SSLC", "流式消息库"),
    "tidb-platform": ("ORD", "交易订单系统JYDD", "分布式订单库"),
    "warehouse": ("WHS", "数仓服务系统SCFW", "宽表明细库"),
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
    {"type": "mysql", "code": "acc", "user": "account-team-prod", "subsystem": "account", "environments": ("prod",), "weight": 18},
    {"type": "mysql", "code": "pay", "user": "payment-team-prod", "subsystem": "payment", "environments": ("prod", "staging"), "weight": 16},
    {"type": "mysql", "code": "bil", "user": "billing-team-prod", "subsystem": "billing", "environments": ("prod", "staging"), "weight": 12},
    {"type": "tidb", "code": "ord", "user": "order-team-prod", "subsystem": "order", "environments": ("prod", "staging"), "weight": 14},
    {"type": "tidb", "code": "inv", "user": "inventory-team-prod", "subsystem": "inventory", "environments": ("prod", "staging"), "weight": 12},
    {"type": "kafka", "code": "trd", "user": "trade-team-staging", "subsystem": "trade", "environments": ("staging", "perf"), "weight": 10},
    {"type": "kafka", "code": "msg", "user": "messaging-team-staging", "subsystem": "messaging", "environments": ("staging", "perf"), "weight": 8},
    {"type": "influxdb", "code": "mon", "user": "monitor-team-prod", "subsystem": "monitor", "environments": ("prod", "perf"), "weight": 10},
    {"type": "redis", "code": "ses", "user": "session-team-prod", "subsystem": "session", "environments": ("prod", "staging", "dev"), "weight": 10},
    {"type": "redis", "code": "prf", "user": "profile-team-prod", "subsystem": "profile", "environments": ("prod", "staging", "dev"), "weight": 8},
    {"type": "mongodb", "code": "cnt", "user": "content-team-staging", "subsystem": "content", "environments": ("staging", "dev"), "weight": 8},
    {"type": "mongodb", "code": "grw", "user": "growth-team-staging", "subsystem": "growth", "environments": ("staging", "dev"), "weight": 8},
    {"type": "elasticsearch", "code": "sea", "user": "search-team-staging", "subsystem": "search", "environments": ("staging", "perf"), "weight": 6},
    {"type": "elasticsearch", "code": "rec", "user": "recommend-team-staging", "subsystem": "recommend", "environments": ("staging", "perf"), "weight": 6},
    {"type": "clickhouse", "code": "whs", "user": "warehouse-team-prod", "subsystem": "warehouse", "environments": ("prod", "perf"), "weight": 6},
    {"type": "clickhouse", "code": "ana", "user": "analytics-team-prod", "subsystem": "analytics", "environments": ("prod", "perf"), "weight": 6},
]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UNIT_IDS.clear()
    SITE_IDS.clear()
    HOST_IDS.clear()
    CLUSTER_IDS.clear()
    AREA_IDS.clear()

    sites = build_sites()
    clusters = build_clusters(sites)
    hosts = build_hosts(sites, clusters)
    services = build_services(sites, clusters, hosts)
    refresh_host_seed_allocations(hosts, services)
    public_services = public_service_seed_data(services, sites, hosts)
    backups = build_backups(public_services)
    for site in sites:
        site.pop("_logicalId", None)
    for host in hosts:
        host.pop("_logicalId", None)

    write_json(DATA_DIR / "sites.json", sites)
    write_json(DATA_DIR / "clusters.json", clusters)
    write_json(DATA_DIR / "hosts.json", hosts)
    write_json(DATA_DIR / "services.json", public_services)
    write_json(DATA_DIR / "backups.json", backups)


def build_sites() -> list[dict[str, Any]]:
    """Build raw site seed data."""

    site_rows = [
        ("site-prod-sh-01", "上海PIT站", "prod", "cn-east-1", "cn-east-1a"),
        ("site-prod-sh-02", "上海张江站", "prod", "cn-east-1", "cn-east-1b"),
        ("site-prod-bj-01", "北京亦庄站", "prod", "cn-north-1", "cn-north-1a"),
        ("site-prod-gz-01", "广州南沙站", "prod", "cn-south-1", "cn-south-1a"),
        ("site-staging-sh-01", "上海验证站", "staging", "cn-east-1", "cn-east-1c"),
        ("site-staging-bj-01", "北京验证站", "staging", "cn-north-1", "cn-north-1b"),
        ("site-staging-gz-01", "广州验证站", "staging", "cn-south-1", "cn-south-1b"),
        ("site-dev-hz-01", "杭州研发站", "dev", "cn-east-2", "cn-east-2a"),
        ("site-dev-sz-01", "深圳研发站", "dev", "cn-south-2", "cn-south-2a"),
        ("site-perf-sh-01", "上海压测站", "perf", "cn-east-1", "cn-east-1d"),
        ("site-perf-bj-01", "北京压测站", "perf", "cn-north-1", "cn-north-1c"),
        ("site-dr-sh-01", "上海灾备站", "prod", "cn-east-1", "cn-east-1e"),
    ]

    sites: list[dict[str, Any]] = []
    for sequence, (site_id, name, environment, region, zone) in enumerate(site_rows):
        sites.append(
            {
                "id": random_u32_decimal_id(SITE_IDS),
                "_logicalId": site_id,
                "name": name,
                "areaId": random_u32_decimal_id(AREA_IDS),
                "environment": environment,
                "region": region,
                "zone": zone,
                "areaName": ("核心区", "转接区", "容灾区", "验证区")[sequence % 4],
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
            cluster_type = ("KUBERNETES", "KUBERNETES", "BAREMETAL", "KUBERNETES")[cluster_index]
            scheduler = {"KUBERNETES": "K8S", "BAREMETAL": "SYSTEMD"}[cluster_type]
            clusters.append(
                {
                    "id": random_u32_decimal_id(CLUSTER_IDS),
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
            logical_host_id = f"host-{cluster_sequence:02d}-{host_index + 1:02d}"
            host_id = random_u32_decimal_id(HOST_IDS)
            ip = f"192.18.{10 + cluster_sequence}.{10 + host_index + 1}"
            cpu_capacity = float(RNG.choice((24, 32, 48, 64, 96, 128)))
            memory_capacity = round(cpu_capacity * RNG.choice((4.0, 5.0, 6.0, 8.0)), 1)
            cpu_allocated = round(cpu_capacity * RNG.uniform(0.04, 0.38), 1)
            memory_allocated = round(memory_capacity * RNG.uniform(0.05, 0.42), 1)
            status, health_status = compute_host_runtime_state(cluster_sequence, host_index)
            arch, arch_name, _build_suffix = choose_architecture(host_id)
            creator, creator_name = choose_owner(f"host:{host_id}")
            created_at = (
                datetime(2026, 4, 1, 9, 0, 0)
                + timedelta(days=stable_index(f"host-created-day:{host_id}") % 55)
                + timedelta(minutes=stable_index(f"host-created-minute:{host_id}") % 480)
            ).strftime("%Y-%m-%d %H:%M:%S")
            cpu_resource = build_resource_summary(cpu_capacity, cpu_allocated)
            memory_resource = build_resource_summary(
                memory_capacity,
                memory_allocated,
                capacity_key="capacityGB",
                allocated_key="allocatedGB",
                available_key="availableGB",
            )
            storage = build_host_storage(host_id)
            hosts.append(
                {
                    "id": host_id,
                    "_logicalId": logical_host_id,
                    "name": f"syn47{cluster_sequence:02d}{host_index + 1000:04d}",
                    "ip": ip,
                    "sshPort": RNG.choice((22, 2222)),
                    "siteId": site["id"],
                    "siteName": site["name"],
                    "clusterId": cluster["id"],
                    "clusterEnabled": True,
                    "areaId": site["areaId"],
                    "areaName": site["areaName"],
                    "room": f"{site['region'].upper()}-ROOM-{(host_index // 20) + 1:02d}",
                    "seat": f"{site['region'].upper()}-{(host_index // 10) + 1:02d}-{host_index % 10 + 1:02d}",
                    "networkPartition": f"ha-{chr(ord('a') + host_index % 3)}",
                    "status": status,
                    "healthStatus": health_status,
                    "cpuArchitecture": arch,
                    "cpuArchitectureName": arch_name,
                    "cpuCapacityCores": cpu_resource["capacityCores"],
                    "cpuAllocatedCores": cpu_resource["allocatedCores"],
                    "cpuAvailableCores": cpu_resource["availableCores"],
                    "cpuAllocationPercent": cpu_resource["allocationPercent"],
                    "memoryCapacityGB": memory_resource["capacityGB"],
                    "memoryAllocatedGB": memory_resource["allocatedGB"],
                    "memoryAvailableGB": memory_resource["availableGB"],
                    "memoryAllocationPercent": memory_resource["allocationPercent"],
                    "hdd": storage["hdd"],
                    "ssd": storage["ssd"],
                    "sanName": storage["sanName"],
                    "maxUnitCount": int(RNG.choice((40, 50, 60, 80, 100))),
                    "maxUsagePercent": float(RNG.choice((80, 85, 90, 95, 100))),
                    "unitCount": 0,
                    "createdAt": created_at,
                    "creator": creator,
                    "creatorName": creator_name,
                }
            )
    return hosts


def build_resource_summary(
    capacity: float,
    allocated: float,
    *,
    capacity_key: str = "capacityCores",
    allocated_key: str = "allocatedCores",
    available_key: str = "availableCores",
) -> dict[str, float]:
    """Build a resource summary with stable rounded values."""

    allocated = min(capacity, allocated)
    available = max(0.0, capacity - allocated)
    return {
        capacity_key: round(capacity, 1),
        allocated_key: round(allocated, 1),
        available_key: round(available, 1),
        "allocationPercent": round(allocated / capacity * 100, 1) if capacity else 0.0,
    }


def build_storage_device(device: str, capacity_options: tuple[float, ...], used_ratio: tuple[float, float]) -> dict[str, float | str]:
    """Build one host storage device summary."""

    capacity = float(RNG.choice(capacity_options))
    used = round(capacity * RNG.uniform(*used_ratio), 1)
    available = max(0.0, capacity - used)
    return {
        "device": device,
        "capacityGB": round(capacity, 1),
        "usedGB": used,
        "availableGB": round(available, 1),
        "usagePercent": round(used / capacity * 100, 1) if capacity else 0.0,
    }


def build_host_storage(host_id: str) -> dict[str, Any]:
    """Build host storage summary."""

    profile = stable_index(f"storage:{host_id}") % 10
    hdd = None
    ssd = None
    if profile % 2 == 0:
        hdd = build_storage_device(
            "/dev/sdb",
            (2048.0, 4096.0, 8192.0, 12288.0, 16384.0),
            (0.08, 0.36),
        )
    else:
        ssd = build_storage_device(
            "/dev/nvme0n1",
            (1024.0, 2048.0, 4096.0, 8192.0),
            (0.06, 0.32),
        )
    return {
        "hdd": hdd,
        "ssd": ssd,
        "sanName": f"san-{host_id[-5:]}" if profile == 7 else None,
    }


def build_services(
    sites: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    hosts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build raw service-group seed data."""

    site_by_id = {site["id"]: site for site in sites}
    site_by_logical_id = {site["_logicalId"]: site for site in sites}
    cluster_by_id = {cluster["id"]: cluster for cluster in clusters}
    global HOST_BY_ID
    host_by_id = {host["id"]: host for host in hosts}
    host_by_logical_id = {host["_logicalId"]: host for host in hosts}
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
            and host_by_id[host_id]["status"] == "enabled"
        ]
        offset = sum(ord(char) for char in key) % len(eligible_host_ids)
        return host_by_id[eligible_host_ids[offset]]

    def host_id_for_logical_id(logical_id: str) -> str:
        return str(host_by_logical_id[logical_id]["id"])

    services: list[dict[str, Any]] = []

    services.append(
        build_mysql_service(
            name=MYSQL_ANCHOR_SERVICE_NAME,
            site=site_by_logical_id["site-prod-sh-01"],
            user="payment-platform-team",
            subsystem="payment-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
            explicit_hosts={
                "mysql-primary-01": host_id_for_logical_id("host-01-01"),
                "mysql-replica-01": host_id_for_logical_id("host-01-05"),
                "proxy-01": host_id_for_logical_id("host-02-03"),
                "proxy-02": host_id_for_logical_id("host-02-04"),
                "sm-01": host_id_for_logical_id("host-02-05"),
            },
        )
    )
    services.append(
        build_tidb_service(
            name=TIDB_ANCHOR_SERVICE_NAME,
            site=site_by_logical_id["site-prod-sh-02"],
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
            name="stmad004",
            site=site_by_logical_id["site-prod-sh-01"],
            user="streaming-platform-team",
            subsystem="stream-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_influxdb_service(
            name="monad005",
            site=site_by_logical_id["site-prod-sh-01"],
            user="observability-platform-team",
            subsystem="monitor-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_redis_service(
            name=REDIS_ANCHOR_SERVICE_NAME,
            site=site_by_logical_id["site-prod-sh-01"],
            user="cache-platform-team",
            subsystem="cache-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_mongodb_service(
            name="cntad006",
            site=site_by_logical_id["site-staging-sh-01"],
            user="content-platform-team",
            subsystem="content-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_elasticsearch_service(
            name="seaad007",
            site=site_by_logical_id["site-staging-sh-01"],
            user="search-platform-team",
            subsystem="search-platform",
            next_container_ip=next_container_ip,
            choose_host=choose_healthy_host,
            allow_anomalies=False,
        )
    )
    services.append(
        build_clickhouse_service(
            name="whsad008",
            site=site_by_logical_id["site-prod-bj-01"],
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

    pattern_serials: dict[str, int] = {}
    for index in range(GENERATED_SERVICE_COUNT):
        pattern = weighted_patterns[index % len(weighted_patterns)]
        environment = pattern["environments"][index % len(pattern["environments"])]
        site = environments_to_sites[environment][index % len(environments_to_sites[environment])]
        pattern_key = f"{pattern['code']}:{environment}"
        pattern_serials[pattern_key] = pattern_serials.get(pattern_key, 0) + 1
        name = generated_service_name(
            code=pattern["code"],
            environment=environment,
            serial=pattern_serials[pattern_key],
        )
        if pattern["type"] == "mysql":
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


def public_service_seed_data(
    services: list[dict[str, Any]],
    sites: list[dict[str, Any]],
    hosts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project internal generated services to the public services schema shape."""

    site_by_id = {site["id"]: site for site in sites}
    host_by_id = {host["id"]: host for host in hosts}
    public_services: list[dict[str, Any]] = []
    for service in services:
        site = site_by_id[service["siteId"]]
        child_type_counts: dict[str, int] = {}
        child_services: list[dict[str, Any]] = []
        for child_service in service.get("services", []):
            child_service_type = child_service["type"]
            child_type_counts[child_service_type] = child_type_counts.get(child_service_type, 0) + 1
            child_services.append(
                public_child_service(
                    child_service,
                    service_name=service["name"],
                    occurrence=child_type_counts[child_service_type],
                    host_by_id=host_by_id,
                )
            )
        public_services.append(
            {
                "name": service["name"],
                "type": service["type"],
                "user": service.get("user"),
                "ownerAccount": service.get("ownerAccount"),
                "ownerName": service.get("ownerName"),
                "businessSystemName": service.get("businessSystemName"),
                "businessSubsystemName": service.get("businessSubsystemName"),
                "subsystem": service.get("businessSubsystemName") or service.get("subsystem"),
                "siteId": service["siteId"],
                "siteName": site["name"],
                "areaName": service.get("areaName") or site.get("areaName"),
                "sharding": service.get("sharding"),
                "runningStatus": public_running_status(service.get("runningStatus") or service.get("healthStatus")),
                "replicationStatus": public_running_status(service.get("replicationStatus")),
                "childServices": child_services,
                "backupStrategy": service.get("backupStrategy"),
            }
        )
    return public_services


def public_child_service(
    child_service: dict[str, Any],
    *,
    service_name: str,
    occurrence: int,
    host_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project an internal child service to the public services schema shape."""

    return {
        "name": child_service_public_name(service_name, child_service["type"], occurrence),
        "type": child_service["type"],
        "version": child_service.get("version"),
        "port": child_service.get("port"),
        "runningStatus": public_running_status(child_service.get("runningStatus") or child_service.get("healthStatus")),
        "units": [
            public_service_unit(unit, host_by_id=host_by_id)
            for unit in child_service.get("units", [])
        ],
    }


def child_service_public_name(service_name: str, child_service_type: str, occurrence: int) -> str:
    """Return a stable public child-service name."""

    return f"{service_name}-{child_service_type}-{occurrence:02d}"


def public_service_unit(
    unit: dict[str, Any],
    *,
    host_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project an internal unit to the public services schema shape."""

    host = host_by_id[unit["hostId"]]
    return {
        "name": unit["name"],
        "type": unit["type"],
        "cpuArchitecture": unit.get("cpuArchitecture"),
        "cpuArchitectureDisplayName": unit.get("cpuArchitectureDisplayName"),
        "version": unit.get("version"),
        "runningStatus": public_running_status(unit.get("runningStatus") or unit.get("healthStatus")),
        "hostName": host["name"],
        "hostIp": host["ip"],
        "ip": unit.get("ip") or unit.get("containerIp"),
        "ipv6": unit.get("ipv6"),
        "cpu": unit.get("cpu"),
        "memoryGB": unit.get("memoryGB") or unit.get("memory"),
        "storage": {
            "data": public_volume(unit["storage"]["data"]),
            "log": public_volume(unit["storage"]["log"]),
        },
    }


def public_volume(volume: dict[str, Any]) -> dict[str, Any]:
    """Project an internal volume to the public services schema shape."""

    return {
        "sizeGB": volume.get("sizeGB") or volume.get("size"),
        "type": volume.get("type"),
        "typeDisplayName": volume.get("typeDisplayName"),
    }


def public_running_status(value: Any) -> str | None:
    """Map internal health states to public services running status values."""

    if value is None:
        return None
    normalized = str(value).strip().upper()
    if normalized in {"HEALTHY", "PASSING", "RUNNING", "SUCCESS"}:
        return "passing"
    if normalized in {"WARN", "WARNING", "DEGRADED", "RESTARTING", "MAINTENANCE"}:
        return "warning"
    if normalized in {"UNHEALTHY", "CRITICAL", "FAILED", "FAILURE", "STOPPED"}:
        return "critical"
    return str(value).strip().lower()


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
    arch, arch_name, build_suffix = choose_architecture(name)
    owner_account, owner_name = choose_owner(name)
    business_system_name, business_subsystem_name = choose_business_names(subsystem)
    return {
        "name": name,
        "type": service_type,
        "user": user,
        "subsystem": subsystem,
        "ownerAccount": owner_account,
        "ownerName": owner_name,
        "businessSystemName": business_system_name,
        "businessSubsystemName": business_subsystem_name,
        "siteId": site["id"],
        "areaName": site.get("areaName"),
        "topology": architecture,
        "sharding": sharding,
        "healthStatus": "HEALTHY",
        "runningStatus": "HEALTHY",
        "replicationStatus": "HEALTHY",
        "_buildVersionSuffix": build_suffix,
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
    public_unit_id = random_unit_id()
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
    arch, arch_name, build_suffix = choose_architecture(name)
    container_ip = next_container_ip()

    return {
        "id": public_unit_id,
        "name": unit_display_name(service_name=name, unit_id=unit_id),
        "type": child_service_type,
        "role": role,
        "image": image,
        "version": unit_version(version, build_suffix),
        "cpuArchitecture": arch,
        "cpuArchitectureDisplayName": arch_name,
        "healthStatus": unit_health_status,
        "runningStatus": unit_health_status,
        "containerStatus": container_status,
        "hostId": host["id"],
        "containerIp": container_ip,
        "ip": container_ip,
        "ipv6": unit_ipv6(container_ip),
        "cpu": cpu,
        "memory": memory,
        "storage": {
            "data": {
                "diskId": data_disk["diskId"],
                "mountPoint": f"/dbaas/{child_service_type}/{unit_id}/data",
                "size": data_size,
                "type": f"local:{data_disk['mediaType']}",
                "typeDisplayName": disk_type_display_name(data_disk["mediaType"]),
            },
            "log": {
                "diskId": log_disk["diskId"],
                "mountPoint": f"/dbaas/{child_service_type}/{unit_id}/log",
                "size": log_size,
                "type": f"local:{log_disk['mediaType']}",
                "typeDisplayName": disk_type_display_name(log_disk["mediaType"]),
            },
        },
    }


def compute_host_runtime_state(cluster_sequence: int, host_index: int) -> tuple[str, str]:
    """Compute host runtime state with deterministic anomalies."""

    # Keep the early hosts in the first clusters healthy for stable anchor examples.
    if cluster_sequence <= 2 and host_index < 8:
        return "enabled", "HEALTHY"

    score = (cluster_sequence * 37 + host_index * 17) % 100
    if score < 3:
        return "disabled", "UNHEALTHY"
    if score < 8:
        return "maintenance", "WARN"
    if score < 12:
        return "onboarding", "WARN"
    if score < 16:
        return "offboarding", "WARN"
    if score < 18:
        return "enabled", "WARN"
    return "enabled", "HEALTHY"


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

    host_status = host["status"]
    host_health = host["healthStatus"]
    if host_status == "disabled":
        return "UNHEALTHY", "FAILED"
    if host_status in {"maintenance", "onboarding", "offboarding"}:
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
            child_service["runningStatus"] = "HEALTHY"
            for unit in child_service["units"]:
                unit["healthStatus"] = "HEALTHY"
                unit["runningStatus"] = "HEALTHY"
                unit["containerStatus"] = "RUNNING"
        service["healthStatus"] = "HEALTHY"
        service["runningStatus"] = "HEALTHY"
        service["replicationStatus"] = "HEALTHY"
        return service

    child_healths: list[str] = []
    for child_service in service["services"]:
        unit_healths = [unit["healthStatus"] for unit in child_service["units"]]
        child_service["healthStatus"] = derive_health_status(unit_healths)
        child_service["runningStatus"] = child_service["healthStatus"]
        child_healths.append(child_service["healthStatus"])

    service["healthStatus"] = derive_health_status(child_healths)
    service["runningStatus"] = service["healthStatus"]
    service["replicationStatus"] = service["healthStatus"]
    return service


def pick_host_disk(host: dict[str, Any], *, disk_types: set[str], preferred_media: tuple[str, ...]) -> dict[str, Any]:
    """Pick a disk on the host for a volume."""

    candidates = [disk for disk in host_storage_disks(host) if disk["type"] in disk_types]
    for media_type in preferred_media:
        for disk in candidates:
            if disk["mediaType"] == media_type:
                return disk
    return candidates[0]


def host_storage_disks(host: dict[str, Any]) -> list[dict[str, str | float]]:
    """Return internal disk-like entries derived from the public host storage fields."""

    disks: list[dict[str, str | float]] = []
    ssd = host.get("ssd")
    if isinstance(ssd, dict):
        disks.append(
            {
                "diskId": f"{host['id']}-disk-ssd-01",
                "storageKey": "ssd",
                "type": "data",
                "mediaType": "SSD",
                "capacity": float(ssd["capacityGB"]),
            }
        )
    hdd = host.get("hdd")
    if isinstance(hdd, dict):
        disks.append(
            {
                "diskId": f"{host['id']}-disk-hdd-01",
                "storageKey": "hdd",
                "type": "data",
                "mediaType": "HDD",
                "capacity": float(hdd["capacityGB"]),
            }
        )
    if not disks:
        raise ValueError(f"host '{host['id']}' has no local storage device")
    return disks


def refresh_host_seed_allocations(hosts: list[dict[str, Any]], services: list[dict[str, Any]]) -> None:
    """Backfill host unit count and allocated resources from generated service units."""

    host_by_id = {host["id"]: host for host in hosts}
    disk_by_host_id = {host["id"]: {str(disk["diskId"]): disk for disk in host_storage_disks(host)} for host in hosts}

    for host in hosts:
        host["unitCount"] = 0
        host["cpuAllocatedCores"] = 0.0
        host["memoryAllocatedGB"] = 0.0

    for service in services:
        for child_service in service.get("services", []):
            for unit in child_service.get("units", []):
                host = host_by_id[unit["hostId"]]
                host["unitCount"] += 1
                host["cpuAllocatedCores"] += float(unit.get("cpu") or 0.0)
                host["memoryAllocatedGB"] += float(unit.get("memory") or 0.0)
                for volume_name in ("data", "log"):
                    volume = unit["storage"][volume_name]
                    disk = disk_by_host_id[host["id"]][volume["diskId"]]
                    device = host[str(disk["storageKey"])]
                    if isinstance(device, dict):
                        device["usedGB"] = min(
                            float(device["capacityGB"]),
                            float(device["usedGB"]) + float(volume.get("sizeGB", volume.get("size")) or 0.0),
                        )

    for host in hosts:
        cpu_capacity = float(host["cpuCapacityCores"])
        cpu_allocated = min(cpu_capacity, float(host["cpuAllocatedCores"]))
        host["cpuAllocatedCores"] = round(cpu_allocated, 1)
        host["cpuAvailableCores"] = round(max(0.0, cpu_capacity - cpu_allocated), 1)
        host["cpuAllocationPercent"] = round(cpu_allocated / cpu_capacity * 100, 1) if cpu_capacity else 0.0

        memory_capacity = float(host["memoryCapacityGB"])
        memory_allocated = min(memory_capacity, float(host["memoryAllocatedGB"]))
        host["memoryAllocatedGB"] = round(memory_allocated, 1)
        host["memoryAvailableGB"] = round(max(0.0, memory_capacity - memory_allocated), 1)
        host["memoryAllocationPercent"] = round(memory_allocated / memory_capacity * 100, 1) if memory_capacity else 0.0
        observed_allocation_percent = max(host["cpuAllocationPercent"], host["memoryAllocationPercent"])
        host["maxUsagePercent"] = round(max(float(host["maxUsagePercent"]), observed_allocation_percent), 1)

        for storage_key in ("hdd", "ssd"):
            device = host.get(storage_key)
            if not isinstance(device, dict):
                continue
            capacity = float(device["capacityGB"])
            used = min(capacity, float(device["usedGB"]))
            device["usedGB"] = round(used, 1)
            device["availableGB"] = round(max(0.0, capacity - used), 1)
            device["usagePercent"] = round(used / capacity * 100, 1) if capacity else 0.0

        if host["unitCount"] > host["maxUnitCount"]:
            host["maxUnitCount"] = host["unitCount"] + 5


def data_media_preference(child_service_type: str) -> tuple[str, ...]:
    if child_service_type in {"mysql", "tidb", "tikv", "clickhouse", "elasticsearch", "mongodb", "configsvr", "shard"}:
        return ("SSD", "HDD")
    return ("SSD", "HDD")


def log_media_preference(child_service_type: str) -> tuple[str, ...]:
    if child_service_type in {"mysql", "tidb", "tikv", "clickhouse"}:
        return ("HDD", "SSD")
    return ("HDD", "SSD")


def generated_service_name(*, code: str, environment: str, serial: int) -> str:
    """Generate an 8-character DBAAS-like service name."""

    env_code = {
        "prod": "ad",
        "staging": "st",
        "dev": "dv",
        "perf": "pf",
    }.get(environment, "ad")
    return f"{code[:3]}{env_code}{serial + 100:03d}"


def choose_architecture(seed: str) -> tuple[str, str, str]:
    """Choose a deterministic CPU architecture and build suffix."""

    return ARCHITECTURES[stable_index(seed) % len(ARCHITECTURES)]


def choose_owner(seed: str) -> tuple[str, str]:
    """Choose a deterministic DBAAS owner account and name."""

    account = f"03{stable_index(seed) % 1_000_000:06d}"
    name = OWNER_NAMES[stable_index(f"owner:{seed}") % len(OWNER_NAMES)]
    return account, name


def choose_business_names(subsystem: str) -> tuple[str, str]:
    """Choose meaningful business system names for a subsystem."""

    normalized = subsystem.removesuffix("-platform")
    catalog = BUSINESS_CATALOG.get(subsystem) or BUSINESS_CATALOG.get(normalized)
    if catalog is None:
        catalog = ("DBA", "数据库平台系统DBPT", "数据库服务库")
    return catalog[1], catalog[2]


def unit_display_name(*, service_name: str, unit_id: str) -> str:
    """Generate a production-like unit display name."""

    hex_seed = f"{stable_index(service_name + ':' + unit_id) * 2654435761 & 0xFFFFFFFF:08x}"
    return f"{hex_seed}_{service_name}"


def unit_version(version: str, build_suffix: str) -> str:
    """Return a 4-part unit version while child services keep 3 parts."""

    parts = version.split(".")
    if len(parts) >= 4:
        return ".".join(parts[:4])
    return ".".join([*parts, *("0" for _ in range(3 - len(parts))), build_suffix])


def unit_ipv6(ipv4: str) -> str:
    """Map a deterministic IPv4 container address to a fake IPv6 service address."""

    _first, _second, third, fourth = ipv4.split(".")
    return f"2405:78c0:2000:{int(third):04x}::{int(fourth):x}"


def disk_type_display_name(media_type: str) -> str:
    """Return a Chinese disk display name."""

    if media_type == "SSD":
        return "本地固态盘"
    if media_type == "HDD":
        return "本地机械盘"
    return "本地磁盘"


def stable_index(value: str) -> int:
    """Return a deterministic integer index for a string."""

    return sum(ord(char) for char in value)


def random_unit_id() -> str:
    """Return a globally unique 32-bit random unit ID for seed data."""

    while True:
        value = f"{secrets.randbits(32):08x}"
        if value not in UNIT_IDS:
            UNIT_IDS.add(value)
            return value


def random_u32_decimal_id(used_ids: set[str]) -> str:
    """Return a globally unique 32-bit decimal ID for seed data."""

    while True:
        value = str(secrets.randbelow(2**32 - 1) + 1)
        if value not in used_ids:
            used_ids.add(value)
            return value


def random_unique_hex_id(used_ids: set[str]) -> str:
    """Return a globally unique 32-character random hex ID for seed data."""

    while True:
        value = secrets.token_hex(16)
        if value not in used_ids:
            used_ids.add(value)
            return value


def build_backups(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build backup seed data with both curated and high-volume generic samples."""

    services_by_name = {service["name"]: service for service in services}
    backups: list[dict[str, Any]] = []

    backups.extend(build_mysql_anchor_backups(services_by_name[MYSQL_ANCHOR_SERVICE_NAME]))
    backups.extend(build_tidb_anchor_backups(services_by_name[TIDB_ANCHOR_SERVICE_NAME]))
    backups.extend(build_redis_anchor_backups(services_by_name[REDIS_ANCHOR_SERVICE_NAME]))

    for service_index, service in enumerate(services):
        if service["name"] in SPECIAL_BACKUP_SERVICE_NAMES:
            continue
        backups.extend(build_generic_service_backups(service, service_index=service_index))

    return backups


def build_mysql_anchor_backups(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Build curated MySQL backups used by real-agent backup questions."""

    targets = build_backup_targets(service)
    mysql_primary = select_backup_target(targets, child_service_type="mysql", occurrence=1)
    mysql_replica = select_backup_target(targets, child_service_type="mysql", occurrence=2)
    return [
        make_backup_record(
            service=service,
            target=mysql_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_replica,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_replica,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=mysql_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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


def build_tidb_anchor_backups(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Build curated TiDB backups covering running and failed states."""

    targets = build_backup_targets(service)
    return [
        make_backup_record(
            service=service,
            target=select_backup_target(targets, child_service_type="tikv", occurrence=1),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=select_backup_target(targets, child_service_type="tidb", occurrence=1),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=select_backup_target(targets, child_service_type="tikv", occurrence=2),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=select_backup_target(targets, child_service_type="tikv", occurrence=3),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=select_backup_target(targets, child_service_type="tidb", occurrence=2),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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


def build_redis_anchor_backups(service: dict[str, Any]) -> list[dict[str, Any]]:
    """Build curated Redis backups with mixed statuses."""

    targets = build_backup_targets(service)
    redis_primary = select_backup_target(targets, child_service_type="redis", occurrence=1)
    return [
        make_backup_record(
            service=service,
            target=redis_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=select_backup_target(targets, child_service_type="redis", occurrence=2),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=select_backup_target(targets, child_service_type="redis", occurrence=3),
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            target=redis_primary,
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
            backup_id=random_unique_hex_id(BACKUP_IDS),
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
    child_services = service.get("childServices") or service.get("services") or []
    for child_service in child_services:
        child_type = child_service["type"]
        if preferred_types and child_type not in preferred_types:
            continue
        for unit_index, unit in enumerate(child_service["units"], start=1):
            targets.append(
                {
                    "child_service_name": child_service.get("name")
                    or derive_child_service_name(child_type, unit["name"], unit_index),
                    "child_service_type": child_type,
                    "unit_name": unit["name"],
                    "role": unit.get("role"),
                }
            )

    if targets:
        return targets

    first_child = child_services[0]
    first_unit = first_child["units"][0]
    return [
        {
            "child_service_name": first_child.get("name")
            or derive_child_service_name(first_child["type"], first_unit["name"], 1),
            "child_service_type": first_child["type"],
            "unit_name": first_unit["name"],
            "role": first_unit.get("role"),
        }
    ]


def select_backup_target(
    targets: list[dict[str, str]],
    *,
    child_service_type: str,
    role: str | None = None,
    occurrence: int = 1,
) -> dict[str, str]:
    """Select a backup target by explicit fields instead of semantic unit IDs."""

    matches = [
        target
        for target in targets
        if target["child_service_type"] == child_service_type and (role is None or target["role"] == role)
    ]
    if occurrence < 1 or occurrence > len(matches):
        raise ValueError(f"backup target not found: child_service_type={child_service_type}, role={role}")
    return matches[occurrence - 1]


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
        "task_id": random_unique_hex_id(TASK_IDS),
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

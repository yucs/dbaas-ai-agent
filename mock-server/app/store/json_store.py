"""基于本地 JSON 文件的内存数据存储。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from typing import Any


class ServiceNotFoundError(KeyError):
    """服务组不存在。"""


class ChildServiceTypeNotFoundError(KeyError):
    """服务组中不存在目标子服务类型。"""


class TaskNotFoundError(KeyError):
    """任务不存在。"""


class ServiceUnitNotFoundError(KeyError):
    """子服务中不存在目标单元。"""


class MetricNotFoundError(KeyError):
    """监控项不存在。"""


class MetricCatalogError(ValueError):
    """监控项 catalog 配置错误。"""


class SiteNotFoundError(KeyError):
    """站点不存在。"""


class ClusterNotFoundError(KeyError):
    """集群不存在。"""


class HostNotFoundError(KeyError):
    """主机不存在。"""


class DataValidationError(ValueError):
    """seed 数据关系校验失败。"""


class JsonDataStore:
    """从本地 JSON 文件加载服务数据，并生成平台拓扑视图。"""

    def __init__(self, data_dir: Path, task_unit_interval_seconds: float = 3.0) -> None:
        self.data_dir = data_dir
        self.task_unit_interval_seconds = task_unit_interval_seconds
        self._services_by_name: dict[str, dict[str, Any]] = {}
        self._sites_by_id: dict[str, dict[str, Any]] = {}
        self._clusters_by_id: dict[str, dict[str, Any]] = {}
        self._hosts_by_id: dict[str, dict[str, Any]] = {}
        self._tasks_by_id: dict[str, dict[str, Any]] = {}
        self._metric_catalog_by_key: dict[str, dict[str, Any]] = {}
        self._task_sequence = 0
        self._lock = threading.RLock()
        self.reload()

    def reload(self) -> None:
        """从本地 JSON 文件重新加载内存数据。"""

        with self._lock:
            self._sites_by_id = self._load_sites()
            self._clusters_by_id = self._load_clusters()
            self._hosts_by_id = self._load_hosts()
            self._services_by_name = self._load_services()
            self._metric_catalog_by_key = self._load_metric_catalog()
            self._validate_relationships()
            self._refresh_platform_aggregates()
            self._tasks_by_id = {}
            self._task_sequence = 0

    def get_service_detail(self, name: str) -> dict[str, Any] | None:
        """返回按服务组名称聚合后的服务详情。"""

        with self._lock:
            service = self._services_by_name.get(name)
            if service is None:
                return None
            return self._public_service_detail(service)

    def list_service_details(self, *, user: str | None = None) -> list[dict[str, Any]]:
        """返回当前内存中的服务组详情，可按 user 过滤。"""

        with self._lock:
            return [
                self._public_service_detail(self._services_by_name[name])
                for name in sorted(self._services_by_name)
                if user is None or self._services_by_name[name].get("user") == user
            ]

    def list_users(self, *, user: str | None = None) -> list[dict[str, Any]]:
        """返回用户摘要列表，用户名直接等于服务组 user。"""

        with self._lock:
            users = [
                service_user
                for service_user in sorted(
                    {
                        service.get("user")
                        for service in self._services_by_name.values()
                        if isinstance(service.get("user"), str) and service.get("user")
                    }
                )
                if user is None or service_user == user
            ]
            return [self._public_user_summary(service_user) for service_user in users]

    def get_user(self, user: str) -> dict[str, Any] | None:
        """返回指定用户详情，用户名直接等于服务组 user。"""

        with self._lock:
            user_services = self._list_user_services(user)
            if not user_services:
                return None
            return self._public_user_detail(user, user_services)

    def list_sites(self) -> list[dict[str, Any]]:
        """返回全部站点摘要。"""

        with self._lock:
            return [self._public_site_summary(site_id) for site_id in sorted(self._sites_by_id)]

    def get_site(self, site_id: str) -> dict[str, Any]:
        """返回站点详情。"""

        with self._lock:
            if site_id not in self._sites_by_id:
                raise SiteNotFoundError(site_id)

            site_detail = self._public_site_summary(site_id)
            site_detail["clusters"] = [
                self._public_cluster_summary(cluster_id)
                for cluster_id, cluster in sorted(self._clusters_by_id.items())
                if cluster["siteId"] == site_id
            ]
            site_detail["serviceGroups"] = [
                {
                    "name": service["name"],
                    "type": service["type"],
                    "user": service.get("user"),
                    "subsystem": service["subsystem"],
                    "healthStatus": service["healthStatus"],
                }
                for service in sorted(
                    self._services_by_name.values(),
                    key=lambda item: item["name"],
                )
                if service["siteId"] == site_id
            ]
            return site_detail

    def list_clusters(self) -> list[dict[str, Any]]:
        """返回全部集群摘要。"""

        with self._lock:
            return [self._public_cluster_summary(cluster_id) for cluster_id in sorted(self._clusters_by_id)]

    def get_cluster(self, cluster_id: str) -> dict[str, Any]:
        """返回集群详情。"""

        with self._lock:
            if cluster_id not in self._clusters_by_id:
                raise ClusterNotFoundError(cluster_id)

            cluster_detail = self._public_cluster_summary(cluster_id)
            cluster_detail["hosts"] = [
                self._public_host_summary(host_id)
                for host_id, host in sorted(self._hosts_by_id.items())
                if host["_clusterId"] == cluster_id
            ]
            return cluster_detail

    def list_hosts(self) -> list[dict[str, Any]]:
        """返回全部主机摘要。"""

        with self._lock:
            return [self._public_host_summary(host_id) for host_id in sorted(self._hosts_by_id)]

    def get_host(self, host_id: str) -> dict[str, Any]:
        """返回主机详情。"""

        with self._lock:
            if host_id not in self._hosts_by_id:
                raise HostNotFoundError(host_id)

            host_detail = self._public_host_summary(host_id)
            host_detail["units"] = sorted(self._collect_host_units(host_id), key=lambda item: item["unitId"])
            return host_detail

    def update_service_resources(
        self,
        name: str,
        *,
        child_service_type: str,
        platform_auto: bool | None = None,
        cpu: float | None = None,
        memory: float | None = None,
    ) -> dict[str, Any]:
        """按子服务类型批量更新其下所有 unit 的 CPU 和内存。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)

            for child_service in target_services:
                if platform_auto is not None:
                    child_service["platformAuto"] = platform_auto
                for unit in child_service.get("units", []):
                    if cpu is not None:
                        unit["cpu"] = cpu
                    if memory is not None:
                        unit["memory"] = memory

        return self._get_updated_service_detail(name)

    def update_service_storage(
        self,
        name: str,
        *,
        child_service_type: str,
        platform_auto: bool | None = None,
        data_volume_size: float | None = None,
        log_volume_size: float | None = None,
    ) -> dict[str, Any]:
        """按子服务类型批量更新其下所有 unit 的 data/log 卷规格。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)

            for child_service in target_services:
                if platform_auto is not None:
                    child_service["platformAuto"] = platform_auto
                for unit in child_service.get("units", []):
                    storage = unit["storage"]
                    if data_volume_size is not None:
                        storage["data"]["size"] = data_volume_size
                    if log_volume_size is not None:
                        storage["log"]["size"] = log_volume_size

            self._refresh_platform_aggregates()

        return self._get_updated_service_detail(name)

    def create_service_image_upgrade_task(
        self,
        name: str,
        *,
        child_service_type: str,
        image: str,
        version: str | None = None,
        unit_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """创建镜像升级异步任务。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)
            target_units = self._select_target_units(target_services, unit_ids)
            now = self._utcnow()
            task_id = self._next_task_id()
            selected_unit_ids = [unit["id"] for unit in target_units]
            task = {
                "taskId": task_id,
                "type": "service.image.upgrade",
                "status": "RUNNING",
                "message": "image upgrade running",
                "reason": None,
                "resourceType": "service",
                "resourceName": name,
                "result": None,
                "createdAt": now,
                "updatedAt": now,
                "_operation": {
                    "kind": "service.image.upgrade",
                    "childServiceType": child_service_type,
                    "image": image,
                    "version": version,
                    "unitIds": selected_unit_ids,
                },
            }
            self._tasks_by_id[task_id] = task
        self._start_task_worker(task_id)
        return self._public_task(task)

    def get_task(self, task_id: str) -> dict[str, Any]:
        """查询通用异步任务详情。"""

        with self._lock:
            task = self._tasks_by_id.get(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            return self._public_task(task)

    def list_latest_metric_points(
        self,
        metric_key: str,
        *,
        service_name: str | None = None,
        owner_user: str | None = None,
        total_count: int = 100_000,
    ) -> list[dict[str, Any]]:
        """按监控项动态生成最新监控点位。"""

        with self._lock:
            metric = self._get_metric_catalog_item(metric_key)
            real_units = self._collect_metric_units(
                service_name=service_name,
                owner_user=owner_user,
                metric=metric,
            )
            if service_name is not None and service_name not in self._services_by_name:
                raise ServiceNotFoundError(service_name)

            records = [
                {
                    "unit_name": item["unit_name"],
                    "service_type": item["service_type"],
                    "value": self._metric_value(metric, item, ordinal),
                }
                for ordinal, item in enumerate(real_units)
            ]
            if owner_user is not None and not real_units:
                return records

            fake_service_types = self._fake_service_types(metric, service_name=service_name, owner_user=owner_user)
            fake_count = max(0, total_count - len(records))
            for fake_index in range(fake_count):
                service_type = fake_service_types[fake_index % len(fake_service_types)]
                item = self._fake_metric_unit(
                    service_type,
                    fake_index,
                    service_name=service_name,
                    owner_user=owner_user,
                )
                records.append(
                    {
                        "unit_name": item["unit_name"],
                        "service_type": item["service_type"],
                        "value": self._metric_value(metric, item, len(real_units) + fake_index),
                    }
                )
            return records

    def list_unit_metric_history(
        self,
        unit_name: str,
        metric_key: str,
        *,
        start_ts: int,
        end_ts: int,
    ) -> list[dict[str, Any]]:
        """按单元和监控项动态生成历史监控点位。"""

        with self._lock:
            metric = self._get_metric_catalog_item(metric_key)
            unit = self._select_history_metric_unit(unit_name, metric)
            duration = end_ts - start_ts
            step_seconds = max(60, math.ceil(duration / 720))
            points: list[dict[str, Any]] = []
            for ts in range(start_ts, end_ts + 1, step_seconds):
                points.append({"ts": ts, "value": self._metric_value(metric, unit, ts, ts=ts)})
            return points

    def find_unit_bindings(self, unit_name: str) -> list[dict[str, Any]]:
        """返回真实单元名称对应的服务归属。"""

        with self._lock:
            bindings: list[dict[str, Any]] = []
            for service in self._services_by_name.values():
                for child_service in service.get("services", []):
                    for unit in child_service.get("units", []):
                        if unit.get("name") != unit_name:
                            continue
                        bindings.append(
                            {
                                "service_name": service["name"],
                                "user": service.get("user"),
                                "service_type": child_service["type"],
                                "unit_name": unit["name"],
                            }
                        )
            return bindings

    def _load_sites(self) -> dict[str, dict[str, Any]]:
        """加载站点原始数据。"""

        sites = self._load_array_file(self.data_dir / "sites.json", resource_name="sites")
        sites_by_id: dict[str, dict[str, Any]] = {}
        for site in sites:
            if not isinstance(site, dict):
                raise DataValidationError("sites.json items must be objects")
            site_id = site.get("id")
            if not isinstance(site_id, str) or not site_id:
                raise DataValidationError("each site item must have a non-empty 'id'")
            normalized_site = deepcopy(site)
            normalized_site.setdefault("healthStatus", "HEALTHY")
            normalized_site["clusterCount"] = 0
            normalized_site["hostCount"] = 0
            normalized_site["serviceGroupCount"] = 0
            sites_by_id[site_id] = normalized_site
        return sites_by_id

    def _load_clusters(self) -> dict[str, dict[str, Any]]:
        """加载集群原始数据。"""

        clusters = self._load_array_file(self.data_dir / "clusters.json", resource_name="clusters")
        clusters_by_id: dict[str, dict[str, Any]] = {}
        for cluster in clusters:
            if not isinstance(cluster, dict):
                raise DataValidationError("clusters.json items must be objects")
            cluster_id = cluster.get("id")
            site_id = cluster.get("siteId")
            if not isinstance(cluster_id, str) or not cluster_id:
                raise DataValidationError("each cluster item must have a non-empty 'id'")
            if not isinstance(site_id, str) or not site_id:
                raise DataValidationError(f"cluster '{cluster_id}' must have a non-empty 'siteId'")
            normalized_cluster = deepcopy(cluster)
            normalized_cluster.setdefault("healthStatus", "HEALTHY")
            normalized_cluster["hostCount"] = 0
            normalized_cluster["unitCount"] = 0
            normalized_cluster["serviceGroupCount"] = 0
            clusters_by_id[cluster_id] = normalized_cluster
        return clusters_by_id

    def _load_hosts(self) -> dict[str, dict[str, Any]]:
        """加载主机原始数据。"""

        hosts = self._load_array_file(self.data_dir / "hosts.json", resource_name="hosts")
        hosts_by_id: dict[str, dict[str, Any]] = {}
        for host in hosts:
            if not isinstance(host, dict):
                raise DataValidationError("hosts.json items must be objects")
            host_id = host.get("id")
            cluster_id = host.get("clusterId")
            if not isinstance(host_id, str) or not host_id:
                raise DataValidationError("each host item must have a non-empty 'id'")
            if not isinstance(cluster_id, str) or not cluster_id:
                raise DataValidationError(f"host '{host_id}' must have a non-empty 'clusterId'")
            normalized_host = deepcopy(host)
            normalized_host.setdefault("hostStatus", "RUNNING")
            normalized_host.setdefault("healthStatus", "HEALTHY")
            normalized_host["unitCount"] = 0
            disks = normalized_host.get("disks")
            if not isinstance(disks, list) or not disks:
                raise DataValidationError(f"host '{host_id}' must contain a non-empty 'disks' list")
            disk_by_id: dict[str, dict[str, Any]] = {}
            for disk in disks:
                if not isinstance(disk, dict):
                    raise DataValidationError(f"host '{host_id}' disks must be objects")
                disk_id = disk.get("diskId")
                if not isinstance(disk_id, str) or not disk_id:
                    raise DataValidationError(f"host '{host_id}' contains a disk without a valid 'diskId'")
                disk.setdefault("healthStatus", "HEALTHY")
                disk.setdefault("used", 0.0)
                disk["_baseUsed"] = float(disk["used"])
                disk_by_id[disk_id] = disk
            normalized_host["_clusterId"] = cluster_id
            normalized_host["_diskById"] = disk_by_id
            hosts_by_id[host_id] = normalized_host
        return hosts_by_id

    def _load_services(self) -> dict[str, dict[str, Any]]:
        """加载服务组原始数据。"""

        services = self._load_array_file(self.data_dir / "services.json", resource_name="services")
        services_by_name: dict[str, dict[str, Any]] = {}
        for index, service in enumerate(services):
            if not isinstance(service, dict):
                raise DataValidationError("services.json items must be objects")
            name = service.get("name")
            if not isinstance(name, str) or not name:
                raise DataValidationError("each service item must have a non-empty 'name'")
            services_by_name[name] = self._normalize_service_seed(service, index)
        return services_by_name

    def _load_metric_catalog(self) -> dict[str, dict[str, Any]]:
        """加载 AI Agent 侧维护的监控项 catalog。"""

        catalog_path = self.data_dir.parent.parent / "ai-agent" / "backend" / "config" / "dbaas_metric_catalog.json"
        catalog_items = self._load_array_file(catalog_path, resource_name="metric catalog")
        catalog_by_key: dict[str, dict[str, Any]] = {}
        for item in catalog_items:
            if not isinstance(item, dict):
                raise DataValidationError("dbaas_metric_catalog.json items must be objects")
            metric_key = item.get("metric_key")
            if not isinstance(metric_key, str) or not metric_key:
                raise DataValidationError("each metric catalog item must have a non-empty 'metric_key'")
            if re.fullmatch(r"[a-zA-Z0-9._-]+", metric_key) is None:
                raise DataValidationError(f"metric_key '{metric_key}' contains unsupported characters")
            if metric_key in catalog_by_key:
                raise DataValidationError(f"duplicate metric_key '{metric_key}' in dbaas_metric_catalog.json")

            value_type = item.get("value_type")
            if value_type not in {"number", "string", "enum", "boolean"}:
                raise DataValidationError(f"metric_key '{metric_key}' has unsupported value_type '{value_type}'")
            if value_type == "enum":
                enum_values = item.get("enum_values")
                if not isinstance(enum_values, list) or not enum_values or not all(isinstance(value, str) for value in enum_values):
                    raise DataValidationError(f"metric_key '{metric_key}' must define non-empty string enum_values")
            service_types = item.get("service_types")
            if not isinstance(service_types, list) or not all(isinstance(value, str) and value for value in service_types):
                raise DataValidationError(f"metric_key '{metric_key}' must define service_types")
            catalog_by_key[metric_key] = deepcopy(item)
        return catalog_by_key

    def _load_array_file(self, file_path: Path, *, resource_name: str) -> list[Any]:
        """从 JSON 文件中读取数组。"""

        if not file_path.exists():
            raise DataValidationError(f"missing required seed file: {file_path.name}")

        with file_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, list):
            raise DataValidationError(f"{file_path.name} must contain a JSON array for {resource_name}")
        return payload

    def _normalize_service_seed(self, service: dict[str, Any], service_index: int) -> dict[str, Any]:
        """规范化服务组 seed。"""

        normalized_service = deepcopy(service)
        site_id = normalized_service.get("siteId")
        if not isinstance(site_id, str) or not site_id:
            raise DataValidationError(f"service '{normalized_service.get('name')}' must have a non-empty 'siteId'")

        raw_user = normalized_service.get("user")
        raw_owner = normalized_service.pop("owner", None)
        if raw_user is None:
            raw_user = raw_owner
        elif raw_owner is not None and raw_owner != raw_user:
            raise DataValidationError(
                f"service '{normalized_service.get('name')}' has mismatched 'user' and legacy 'owner'"
            )
        if raw_user is not None and (not isinstance(raw_user, str) or not raw_user):
            raise DataValidationError(
                f"service '{normalized_service.get('name')}' must have a non-empty 'user' when provided"
            )
        normalized_service["user"] = raw_user

        normalized_service.setdefault("healthStatus", "HEALTHY")
        normalized_service.setdefault("subsystem", self._derive_subsystem(normalized_service))
        normalized_service.setdefault("network", self._build_fallback_service_network(site_id, service_index))

        services = normalized_service.get("services")
        if not isinstance(services, list):
            raise DataValidationError(f"service '{normalized_service['name']}' must contain a 'services' list")

        for child_service in services:
            if not isinstance(child_service, dict):
                raise DataValidationError(f"service '{normalized_service['name']}' child services must be objects")
            child_service.setdefault("healthStatus", "HEALTHY")
            child_service.setdefault("platformAuto", None)
            units = child_service.get("units")
            if not isinstance(units, list):
                raise DataValidationError(
                    f"child service '{child_service.get('type')}' in service '{normalized_service['name']}' must contain a 'units' list"
                )
            for unit in units:
                if not isinstance(unit, dict):
                    raise DataValidationError(
                        f"units in child service '{child_service.get('type')}' of service '{normalized_service['name']}' must be objects"
                    )
                host_id = unit.get("hostId")
                if not isinstance(host_id, str) or not host_id:
                    raise DataValidationError(
                        f"unit '{unit.get('id')}' in service '{normalized_service['name']}' must have a non-empty 'hostId'"
                    )
                unit.setdefault("healthStatus", "HEALTHY")
                unit.setdefault("containerStatus", "RUNNING")
                unit["storage"] = self._normalize_unit_storage_seed(unit.get("storage"))

        return normalized_service

    def _normalize_unit_storage_seed(self, storage: Any) -> dict[str, Any]:
        """规范化 seed 中的 unit 存储结构。"""

        if not isinstance(storage, dict):
            raise DataValidationError("unit storage must be an object with 'data' and 'log'")

        if "data" in storage and "log" in storage:
            return {
                "data": self._normalize_volume_seed(storage["data"], volume_name="data"),
                "log": self._normalize_volume_seed(storage["log"], volume_name="log"),
            }

        return {
            "data": self._normalize_volume_seed(
                {"size": storage.get("dataVolumeSize"), "diskId": storage.get("dataDiskId")},
                volume_name="data",
            ),
            "log": self._normalize_volume_seed(
                {"size": storage.get("logVolumeSize"), "diskId": storage.get("logDiskId")},
                volume_name="log",
            ),
        }

    def _normalize_volume_seed(self, volume: Any, *, volume_name: str) -> dict[str, Any]:
        """规范化 seed 中的 volume 结构。"""

        if not isinstance(volume, dict):
            raise DataValidationError(f"unit storage volume '{volume_name}' must be an object")
        disk_id = volume.get("diskId")
        mount_point = volume.get("mountPoint")
        size = volume.get("size")
        if not isinstance(disk_id, str) or not disk_id:
            raise DataValidationError(f"unit storage volume '{volume_name}' must have a non-empty 'diskId'")
        if not isinstance(mount_point, str) or not mount_point:
            mount_point = f"/dbaas/{volume_name}"
        if size is None:
            raise DataValidationError(f"unit storage volume '{volume_name}' must have 'size'")
        return {
            "diskId": disk_id,
            "mountPoint": mount_point,
            "size": float(size),
        }

    def _derive_subsystem(self, service: dict[str, Any]) -> str:
        """推导服务组所属子系统。"""

        subsystem = service.get("subsystem")
        if isinstance(subsystem, str) and subsystem:
            return subsystem

        user = service.get("user")
        if user is None:
            user = service.get("owner")
        if isinstance(user, str):
            if "-team-" in user:
                return user.split("-team-", 1)[0]
            if user.startswith("team-") and len(user) > len("team-"):
                return f"{user[len('team-'):]}-platform"

        service_type = service.get("type")
        if isinstance(service_type, str) and service_type:
            return f"{service_type}-platform"
        return "dbaas-platform"

    def _build_fallback_service_network(self, site_id: str, service_index: int) -> dict[str, str]:
        """为缺失 network 的服务组补一个默认网段。"""

        site = self._sites_by_id.get(site_id)
        if site is None:
            third_octet = 10 + service_index % 200
            return {
                "vpcId": "vpc-fallback",
                "subnetId": f"subnet-fallback-{service_index:04d}",
                "cidr": f"192.168.{third_octet}.0/24",
                "gateway": f"192.168.{third_octet}.1",
            }

        site_sequence = int(site.get("sequence", 0))
        third_octet = 10 + site_sequence * 16 + service_index % 16
        return {
            "vpcId": f"vpc-{site['environment']}-{site['region']}",
            "subnetId": f"subnet-{site_id}-{service_index % 16:02d}",
            "cidr": f"192.168.{third_octet}.0/24",
            "gateway": f"192.168.{third_octet}.1",
        }

    def _validate_relationships(self) -> None:
        """校验 seed 之间的引用关系。"""

        for cluster_id, cluster in self._clusters_by_id.items():
            site_id = cluster["siteId"]
            if site_id not in self._sites_by_id:
                raise DataValidationError(f"cluster '{cluster_id}' references unknown site '{site_id}'")

        for host_id, host in self._hosts_by_id.items():
            cluster_id = host["_clusterId"]
            cluster = self._clusters_by_id.get(cluster_id)
            if cluster is None:
                raise DataValidationError(f"host '{host_id}' references unknown cluster '{cluster_id}'")
            site = self._sites_by_id[cluster["siteId"]]
            host["_siteId"] = site["id"]
            host["_siteName"] = site["name"]
            host["_clusterName"] = cluster["name"]
            host["_environment"] = site["environment"]
            host["_region"] = site["region"]
            host["_zone"] = site["zone"]

        for service_name, service in self._services_by_name.items():
            site_id = service["siteId"]
            if site_id not in self._sites_by_id:
                raise DataValidationError(f"service '{service_name}' references unknown site '{site_id}'")
            for child_service in service.get("services", []):
                for unit in child_service.get("units", []):
                    host_id = unit["hostId"]
                    host = self._hosts_by_id.get(host_id)
                    if host is None:
                        raise DataValidationError(
                            f"unit '{unit.get('id')}' in service '{service_name}' references unknown host '{host_id}'"
                        )
                    if host["_siteId"] != site_id:
                        raise DataValidationError(
                            f"unit '{unit.get('id')}' in service '{service_name}' references host '{host_id}' outside site '{site_id}'"
                        )
                    for volume_name in ("data", "log"):
                        disk_id = unit["storage"][volume_name]["diskId"]
                        if disk_id not in host["_diskById"]:
                            raise DataValidationError(
                                f"unit '{unit.get('id')}' in service '{service_name}' references unknown disk '{disk_id}' on host '{host_id}'"
                            )

    def _refresh_platform_aggregates(self) -> None:
        """重新计算站点、集群、主机层面的聚合信息。"""

        site_service_names: dict[str, set[str]] = {site_id: set() for site_id in self._sites_by_id}
        cluster_service_names: dict[str, set[str]] = {cluster_id: set() for cluster_id in self._clusters_by_id}
        cluster_health_inputs: dict[str, list[str]] = {cluster_id: [] for cluster_id in self._clusters_by_id}
        site_health_inputs: dict[str, list[str]] = {site_id: [] for site_id in self._sites_by_id}

        for site in self._sites_by_id.values():
            site["clusterCount"] = 0
            site["hostCount"] = 0
            site["serviceGroupCount"] = 0
            site["healthStatus"] = site.get("healthStatus", "HEALTHY")

        for cluster in self._clusters_by_id.values():
            cluster["hostCount"] = 0
            cluster["unitCount"] = 0
            cluster["serviceGroupCount"] = 0
            cluster["healthStatus"] = cluster.get("healthStatus", "HEALTHY")

        for host in self._hosts_by_id.values():
            host["unitCount"] = 0
            for disk in host["disks"]:
                disk["used"] = float(disk.get("_baseUsed", 0.0))

        for cluster in self._clusters_by_id.values():
            site = self._sites_by_id[cluster["siteId"]]
            site["clusterCount"] += 1

        for host in self._hosts_by_id.values():
            cluster = self._clusters_by_id[host["_clusterId"]]
            cluster["hostCount"] += 1
            self._sites_by_id[cluster["siteId"]]["hostCount"] += 1
            cluster_health_inputs[cluster["id"]].append(host["healthStatus"])

        for service in self._services_by_name.values():
            site_service_names[service["siteId"]].add(service["name"])
            service_cluster_ids: set[str] = set()
            for child_service in service.get("services", []):
                for unit in child_service.get("units", []):
                    host = self._hosts_by_id[unit["hostId"]]
                    cluster = self._clusters_by_id[host["_clusterId"]]
                    host["unitCount"] += 1
                    cluster["unitCount"] += 1
                    cluster_service_names[cluster["id"]].add(service["name"])
                    service_cluster_ids.add(cluster["id"])
                    for volume_name in ("data", "log"):
                        volume = unit["storage"][volume_name]
                        disk = host["_diskById"][volume["diskId"]]
                        disk["used"] = min(float(disk["capacity"]), float(disk["used"]) + float(volume["size"]))
            for cluster_id in service_cluster_ids:
                cluster_health_inputs[cluster_id].append(service["healthStatus"])
            site_health_inputs[service["siteId"]].append(service["healthStatus"])

        for cluster_id, cluster in self._clusters_by_id.items():
            cluster["serviceGroupCount"] = len(cluster_service_names[cluster_id])
            cluster["healthStatus"] = self._aggregate_health_status(cluster_health_inputs[cluster_id])
            site_health_inputs[cluster["siteId"]].append(cluster["healthStatus"])

        for site_id, site in self._sites_by_id.items():
            site["serviceGroupCount"] = len(site_service_names[site_id])
            site["healthStatus"] = self._aggregate_health_status(site_health_inputs[site_id])

    def _public_site_summary(self, site_id: str) -> dict[str, Any]:
        """返回站点摘要。"""

        site = self._sites_by_id[site_id]
        return {
            "id": site["id"],
            "name": site["name"],
            "environment": site["environment"],
            "region": site["region"],
            "zone": site["zone"],
            "healthStatus": site["healthStatus"],
            "clusterCount": site["clusterCount"],
            "hostCount": site["hostCount"],
            "serviceGroupCount": site["serviceGroupCount"],
        }

    def _public_cluster_summary(self, cluster_id: str) -> dict[str, Any]:
        """返回集群摘要。"""

        cluster = self._clusters_by_id[cluster_id]
        site = self._sites_by_id[cluster["siteId"]]
        return {
            "id": cluster["id"],
            "name": cluster["name"],
            "siteId": site["id"],
            "siteName": site["name"],
            "environment": site["environment"],
            "region": site["region"],
            "zone": site["zone"],
            "clusterType": cluster["clusterType"],
            "scheduler": cluster["scheduler"],
            "healthStatus": cluster["healthStatus"],
            "hostCount": cluster["hostCount"],
            "unitCount": cluster["unitCount"],
            "serviceGroupCount": cluster["serviceGroupCount"],
        }

    def _public_host_summary(self, host_id: str) -> dict[str, Any]:
        """返回主机摘要。"""

        host = self._hosts_by_id[host_id]
        public_host = deepcopy(host)
        public_host["siteId"] = host["_siteId"]
        public_host["siteName"] = host["_siteName"]
        public_host["clusterId"] = host["_clusterId"]
        public_host["clusterName"] = host["_clusterName"]
        public_host["environment"] = host["_environment"]
        public_host["region"] = host["_region"]
        public_host["zone"] = host["_zone"]
        public_host["disks"] = [self._public_disk(disk) for disk in public_host["disks"]]
        public_host.pop("_siteId", None)
        public_host.pop("_siteName", None)
        public_host.pop("_clusterId", None)
        public_host.pop("_clusterName", None)
        public_host.pop("_environment", None)
        public_host.pop("_region", None)
        public_host.pop("_zone", None)
        public_host.pop("_diskById", None)
        return public_host

    def _public_disk(self, disk: dict[str, Any]) -> dict[str, Any]:
        """返回对外暴露的磁盘信息。"""

        public_disk = deepcopy(disk)
        public_disk.pop("_baseUsed", None)
        return public_disk

    def _public_service_detail(self, service: dict[str, Any]) -> dict[str, Any]:
        """返回对外暴露的服务组详情。"""

        site = self._sites_by_id[service["siteId"]]
        public_service = deepcopy(service)
        public_service["environment"] = site["environment"]
        public_service["siteName"] = site["name"]
        public_service["region"] = site["region"]
        public_service["zone"] = site["zone"]
        public_service["user"] = public_service.get("user")
        public_service.pop("owner", None)

        for child_service in public_service.get("services", []):
            for unit in child_service.get("units", []):
                host = self._hosts_by_id[unit["hostId"]]
                unit["hostName"] = host["name"]
                unit["hostIp"] = host["ip"]
                unit["storage"] = self._public_unit_storage(unit["storage"], host["_diskById"])

        return public_service

    def _public_user_summary(self, user: str) -> dict[str, Any]:
        """返回用户摘要。"""

        user_services = self._list_user_services(user)
        return {
            "user": user,
            "serviceGroupCount": len(user_services),
            "environments": sorted(
                {self._sites_by_id[service["siteId"]]["environment"] for service in user_services}
            ),
            "subsystems": sorted({service["subsystem"] for service in user_services}),
        }

    def _public_user_detail(self, user: str, user_services: list[dict[str, Any]]) -> dict[str, Any]:
        """返回用户详情。"""

        user_detail = self._public_user_summary(user)
        user_detail["serviceGroups"] = [
            {
                "name": service["name"],
                "type": service["type"],
                "user": service.get("user"),
                "subsystem": service["subsystem"],
                "healthStatus": service["healthStatus"],
            }
            for service in user_services
        ]
        return user_detail

    def _public_unit_storage(self, storage: dict[str, Any], disk_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """把 seed 里的简化 volume 信息扩成接口响应结构。"""

        public_storage: dict[str, Any] = {}
        for volume_name in ("data", "log"):
            volume = deepcopy(storage[volume_name])
            disk = disk_by_id[volume["diskId"]]
            volume["diskName"] = disk["name"]
            volume["diskType"] = disk["type"]
            volume["mediaType"] = disk["mediaType"]
            public_storage[volume_name] = volume
        return public_storage

    def _list_user_services(self, user: str) -> list[dict[str, Any]]:
        """返回指定用户拥有的服务组。"""

        return [
            self._services_by_name[name]
            for name in sorted(self._services_by_name)
            if self._services_by_name[name].get("user") == user
        ]

    def _collect_host_units(self, host_id: str) -> list[dict[str, Any]]:
        """收集指定主机上的全部单元。"""

        units: list[dict[str, Any]] = []
        for service in self._services_by_name.values():
            for child_service in service.get("services", []):
                for unit in child_service.get("units", []):
                    if unit["hostId"] != host_id:
                        continue
                    units.append(
                        {
                            "serviceName": service["name"],
                            "childServiceType": child_service["type"],
                            "unitId": unit["id"],
                            "unitName": unit["name"],
                            "role": unit["role"],
                            "containerIp": unit["containerIp"],
                            "healthStatus": unit["healthStatus"],
                            "containerStatus": unit["containerStatus"],
                        }
                    )
        return units

    def _collect_metric_units(
        self,
        *,
        service_name: str | None,
        owner_user: str | None,
        metric: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """收集指定监控项适用的真实单元。"""

        services = self._services_by_name.values()
        if service_name is not None:
            service = self._services_by_name.get(service_name)
            if service is None:
                raise ServiceNotFoundError(service_name)
            services = [service]
        elif owner_user is not None:
            services = [
                service
                for service in self._services_by_name.values()
                if service.get("user") == owner_user
            ]

        metric_service_types = set(metric["service_types"])
        items: list[dict[str, Any]] = []
        for service in services:
            for child_service in service.get("services", []):
                service_type = child_service["type"]
                if "container" not in metric_service_types and service_type not in metric_service_types:
                    continue
                for unit in child_service.get("units", []):
                    items.append(
                        {
                            "service_name": service["name"],
                            "service_type": service_type,
                            "unit_name": unit["name"],
                            "unit": unit,
                        }
                    )
        return items

    def _select_history_metric_unit(self, unit_name: str, metric: dict[str, Any]) -> dict[str, Any]:
        """选择真实历史单元；同名时优先选择适配当前 metric 的单元。"""

        matches: list[dict[str, Any]] = []
        for service in self._services_by_name.values():
            for child_service in service.get("services", []):
                for unit in child_service.get("units", []):
                    if unit.get("name") != unit_name:
                        continue
                    matches.append(
                        {
                            "service_name": service["name"],
                            "service_type": child_service["type"],
                            "unit_name": unit["name"],
                            "unit": unit,
                        }
                    )

        if not matches:
            raise ServiceUnitNotFoundError(unit_name)

        metric_service_types = set(metric["service_types"])
        if "container" in metric_service_types:
            return matches[0]
        for item in matches:
            if item["service_type"] in metric_service_types:
                return item
        return matches[0]

    def _fake_service_types(
        self,
        metric: dict[str, Any],
        *,
        service_name: str | None,
        owner_user: str | None,
    ) -> list[str]:
        """返回伪造监控单元可使用的服务类型。"""

        metric_service_types = [value for value in metric["service_types"] if value != "container"]
        if service_name is not None:
            service = self._services_by_name.get(service_name)
            if service is None:
                raise ServiceNotFoundError(service_name)
            child_service_types = [
                child_service["type"]
                for child_service in service.get("services", [])
                if "container" in metric["service_types"] or child_service["type"] in metric_service_types
            ]
            if child_service_types:
                return child_service_types
            return metric_service_types or [service["type"]]
        if owner_user is not None:
            child_service_types = [
                child_service["type"]
                for service in self._services_by_name.values()
                if service.get("user") == owner_user
                for child_service in service.get("services", [])
                if "container" in metric["service_types"] or child_service["type"] in metric_service_types
            ]
            if child_service_types:
                return sorted(set(child_service_types))
            return metric_service_types or ["mysql", "redis", "proxy"]
        return metric_service_types or ["mysql", "redis", "proxy", "tidb", "tikv", "pd"]

    def _fake_metric_unit(
        self,
        service_type: str,
        fake_index: int,
        *,
        service_name: str | None,
        owner_user: str | None,
    ) -> dict[str, Any]:
        """构造一个不落盘的伪造监控单元。"""

        if service_name is None:
            if owner_user is None:
                unit_name = f"mock-{service_type}-{fake_index:06d}"
            else:
                unit_name = f"{owner_user}-mock-{service_type}-{fake_index:06d}"
        else:
            unit_name = f"{service_name}-mock-{fake_index:06d}"
        memory = float(2 ** (fake_index % 5) * 4)
        return {
            "service_name": service_name or f"svc{fake_index % 10_000:04d}",
            "service_type": service_type,
            "unit_name": unit_name,
            "unit": {
                "name": unit_name,
                "version": self._version_for(service_type, fake_index),
                "memory": memory,
                "cpu": float((fake_index % 16) + 1),
            },
        }

    def _get_metric_catalog_item(self, metric_key: str) -> dict[str, Any]:
        """返回指定监控项 catalog 条目。"""

        metric = self._metric_catalog_by_key.get(metric_key)
        if metric is None:
            raise MetricNotFoundError(metric_key)
        return metric

    def _metric_value(
        self,
        metric: dict[str, Any],
        item: dict[str, Any],
        ordinal: int,
        *,
        ts: int | None = None,
    ) -> Any:
        """根据 catalog value_type 生成稳定的 mock 监控值。"""

        value_type = metric["value_type"]
        metric_key = metric["metric_key"]
        seed = self._stable_int(metric_key, item["unit_name"], ordinal, ts or 0)

        if value_type == "number":
            return self._number_metric_value(metric_key, item, seed)
        if value_type == "string":
            return self._string_metric_value(metric_key, item, seed)
        if value_type == "enum":
            enum_values = metric.get("enum_values")
            if not isinstance(enum_values, list) or not enum_values:
                raise MetricCatalogError(f"metric_key '{metric_key}' has invalid enum_values")
            if "passing" in enum_values and seed % 20 < 16:
                return "passing"
            return enum_values[seed % len(enum_values)]
        if value_type == "boolean":
            return seed % 2 == 0
        raise MetricCatalogError(f"metric_key '{metric_key}' has unsupported value_type '{value_type}'")

    def _number_metric_value(self, metric_key: str, item: dict[str, Any], seed: int) -> float | int:
        """生成数字型监控值。"""

        unit = item.get("unit", {})
        memory_gib = float(unit.get("memory") or 8.0)
        if metric_key == "container.cpu.use":
            return round(5 + seed % 940 / 10, 1)
        if metric_key == "container.mem.usagePercent":
            return round(10 + seed % 860 / 10, 1)
        if metric_key == "container.mem.limitBytes":
            return int(memory_gib * 1024 * 1024 * 1024)
        if metric_key == "container.mem.usedBytes":
            usage_percent = 10 + seed % 860 / 10
            return int(memory_gib * 1024 * 1024 * 1024 * usage_percent / 100)
        return round(seed % 10_000 / 10, 1)

    def _string_metric_value(self, metric_key: str, item: dict[str, Any], seed: int) -> str:
        """生成字符串型监控值。"""

        unit = item.get("unit", {})
        if metric_key == "instance.mysql.version":
            version = unit.get("version")
            if isinstance(version, str) and version:
                return version
            return self._version_for(item["service_type"], seed)
        return f"value-{seed % 10_000:04d}"

    def _version_for(self, service_type: str, seed: int) -> str:
        """生成服务类型对应的版本字符串。"""

        if service_type == "mysql":
            return ["8.0.36", "8.0.37", "5.7.44"][seed % 3]
        if service_type == "redis":
            return ["6.2.14", "7.0.15"][seed % 2]
        return f"1.{seed % 8}.{seed % 20}"

    def _stable_int(self, *parts: object) -> int:
        """基于输入生成稳定整数。"""

        payload = "|".join(str(part) for part in parts)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return int(digest[:12], 16)

    def _get_target_child_services(self, name: str, child_service_type: str) -> list[dict[str, Any]]:
        """返回服务组中匹配子服务类型的所有子服务。"""

        service = self._services_by_name.get(name)
        if service is None:
            raise ServiceNotFoundError(name)

        target_services = [
            child_service
            for child_service in service.get("services", [])
            if child_service.get("type") == child_service_type
        ]
        if not target_services:
            raise ChildServiceTypeNotFoundError(child_service_type)
        return target_services

    def _get_updated_service_detail(self, name: str) -> dict[str, Any]:
        """返回更新后的完整服务详情。"""

        updated_service = self.get_service_detail(name)
        if updated_service is None:
            raise ServiceNotFoundError(name)
        return updated_service

    def _select_target_units(self, target_services: list[dict[str, Any]], unit_ids: list[str] | None) -> list[dict[str, Any]]:
        """返回本次任务要操作的单元列表。"""

        units = [unit for child_service in target_services for unit in child_service.get("units", [])]
        if unit_ids is None:
            return units

        units_by_id = {unit["id"]: unit for unit in units}
        missing_unit_ids = [unit_id for unit_id in unit_ids if unit_id not in units_by_id]
        if missing_unit_ids:
            raise ServiceUnitNotFoundError(", ".join(missing_unit_ids))
        return [units_by_id[unit_id] for unit_id in unit_ids]

    def _start_task_worker(self, task_id: str) -> None:
        """启动后台任务执行线程。"""

        worker = threading.Thread(
            target=self._run_task_worker,
            args=(task_id,),
            name=f"mock-task-{task_id}",
            daemon=True,
        )
        worker.start()

    def _run_task_worker(self, task_id: str) -> None:
        """按任务类型执行后台异步任务。"""

        try:
            with self._lock:
                task = self._tasks_by_id.get(task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                operation = deepcopy(task["_operation"])
                task_type = task["type"]

            if task_type == "service.image.upgrade":
                self._run_service_image_upgrade_task(task_id, operation)
                return

            raise ValueError(f"unsupported task type '{task_type}'")
        except Exception as error:  # noqa: BLE001
            self._mark_task_failed(task_id, str(error))

    def _run_service_image_upgrade_task(self, task_id: str, operation: dict[str, Any]) -> None:
        """后台逐个执行镜像升级任务。"""

        for unit_id in operation["unitIds"]:
            time.sleep(self.task_unit_interval_seconds)
            with self._lock:
                task = self._get_task_for_update(task_id)
                unit = self._get_unit_by_id(
                    task["resourceName"],
                    operation["childServiceType"],
                    unit_id,
                )
                unit["image"] = operation["image"]
                if operation["version"] is not None:
                    unit["version"] = operation["version"]
                task["message"] = "image upgrade running"
                task["updatedAt"] = self._utcnow()

        with self._lock:
            task = self._get_task_for_update(task_id)
            task["status"] = "SUCCESS"
            task["message"] = "image upgrade completed"
            task["updatedAt"] = self._utcnow()
            task["result"] = {
                "childServiceType": operation["childServiceType"],
                "unitIds": operation["unitIds"],
                "image": operation["image"],
                "version": operation["version"],
            }

    def _mark_task_failed(self, task_id: str, reason: str) -> None:
        """把任务标记为失败。"""

        with self._lock:
            task = self._tasks_by_id.get(task_id)
            if task is None:
                return
            task["status"] = "FAILED"
            task["message"] = "task execution failed"
            task["reason"] = reason
            task["updatedAt"] = self._utcnow()

    def _get_task_for_update(self, task_id: str) -> dict[str, Any]:
        """返回可写的任务对象。"""

        task = self._tasks_by_id.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _get_unit_by_id(self, name: str, child_service_type: str, unit_id: str) -> dict[str, Any]:
        """返回指定子服务中的目标单元。"""

        target_services = self._get_target_child_services(name, child_service_type)
        for child_service in target_services:
            for unit in child_service.get("units", []):
                if unit.get("id") == unit_id:
                    return unit
        raise ServiceUnitNotFoundError(unit_id)

    def _public_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """返回对外暴露的任务结构。"""

        public_task = deepcopy(task)
        public_task.pop("_operation", None)
        return public_task

    def _aggregate_health_status(self, statuses: list[str]) -> str:
        """聚合健康状态。"""

        if not statuses:
            return "HEALTHY"
        unhealthy_count = sum(1 for status in statuses if status == "UNHEALTHY")
        warn_count = sum(1 for status in statuses if status == "WARN")
        if unhealthy_count == 0 and warn_count == 0:
            return "HEALTHY"
        if unhealthy_count * 2 >= len(statuses):
            return "UNHEALTHY"
        return "WARN"

    def _next_task_id(self) -> str:
        """生成递增任务 ID。"""

        self._task_sequence += 1
        return f"task-{self._task_sequence:04d}"

    def _utcnow(self) -> str:
        """返回当前 UTC 时间字符串。"""

        return self._utcnow_datetime().isoformat().replace("+00:00", "Z")

    def _utcnow_datetime(self) -> datetime:
        """返回当前 UTC 时间。"""

        return datetime.now(UTC)

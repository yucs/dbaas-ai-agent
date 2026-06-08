"""基于本地 JSON 文件的内存数据存储。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any


MOCK_RESOURCE_CPU_CAPACITY_LIMIT = 100.0
MOCK_RESOURCE_MEMORY_CAPACITY_LIMIT_GB = 300.0
MOCK_STORAGE_CAPACITY_LIMIT_GB = 2000.0


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
        self._backups: list[dict[str, Any]] = []
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
            self._backups = self._load_backups()
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

    def get_service_seed(self, name: str) -> dict[str, Any] | None:
        """返回服务组内部 seed 数据，供权限校验和写接口使用。"""

        with self._lock:
            service = self._services_by_name.get(name)
            if service is None:
                return None
            return deepcopy(service)

    def list_service_details(self, *, user: str | None = None) -> list[dict[str, Any]]:
        """返回当前内存中的服务组详情，可按 user 过滤。"""

        with self._lock:
            return [
                self._public_service_detail(self._services_by_name[name])
                for name in sorted(self._services_by_name)
                if user is None or self._services_by_name[name].get("user") == user
            ]

    def list_service_seeds(self, *, user: str | None = None) -> list[dict[str, Any]]:
        """返回当前内存中的服务组内部 seed，可按 user 过滤。"""

        with self._lock:
            return [
                deepcopy(self._services_by_name[name])
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

    def list_backups(self, *, owner_user: str | None = None) -> list[dict[str, Any]]:
        """返回当前仍存在的备份记录，可按所属用户过滤。"""

        with self._lock:
            items: list[dict[str, Any]] = []
            for backup in self._backups:
                if backup.get("deleted") is True:
                    continue
                backup_owner = self._backup_owner_user(backup)
                if owner_user is not None and backup_owner != owner_user:
                    continue
                items.append(self._public_backup_record(backup))
            return items

    def precheck_service_resource_update(
        self,
        name: str,
        *,
        child_service_type: str,
        target_cpu_cores: float | None = None,
        target_memory_gb: float | None = None,
    ) -> dict[str, Any]:
        """返回资源规格调整前的只读预检事实。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)
            units = [unit for child_service in target_services for unit in child_service.get("units", [])]
            current_cpu = float(units[0].get("cpu") or 0.0) if units else 0.0
            current_memory = float(units[0].get("memory") or 0.0) if units else 0.0
            blocking_errors = self._resource_capacity_errors(
                units,
                target_cpu_cores=target_cpu_cores,
                target_memory_gb=target_memory_gb,
            )

            return {
                "service_name": name,
                "child_service_type": child_service_type,
                "current_spec": {
                    "cpu_cores": current_cpu,
                    "memory_gb": current_memory,
                },
                "available_specs": self._available_resource_specs(current_cpu, current_memory),
                "runtime": self._precheck_runtime(units),
                "metrics": {
                    "time_window": "1d",
                    "units": [
                        {
                            "unit_name": unit["name"],
                            "cpu": self._resource_metric_stats("cpu", unit["name"]),
                            "memory": self._resource_metric_stats("memory", unit["name"]),
                        }
                        for unit in units
                    ],
                    "missing_metric_units": [],
                },
                "blocking_errors": blocking_errors,
            }

    def precheck_service_storage_update(
        self,
        name: str,
        *,
        child_service_type: str,
        target_data_volume_gb: float | None = None,
        target_log_volume_gb: float | None = None,
    ) -> dict[str, Any]:
        """返回存储规格调整前的只读预检事实。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)
            units = [unit for child_service in target_services for unit in child_service.get("units", [])]
            current_storage = self._current_storage_spec(units)
            blocking_errors = self._storage_capacity_errors(
                units,
                target_data_volume_gb=target_data_volume_gb,
                target_log_volume_gb=target_log_volume_gb,
            )

            return {
                "service_name": name,
                "child_service_type": child_service_type,
                "current_storage": current_storage,
                "runtime": self._precheck_runtime(units),
                "metrics": {
                    "units": [
                        {
                            "unit_name": unit["name"],
                            "data_usage": self._percent_metric("storage", unit["name"], "data"),
                            "log_usage": self._percent_metric("storage", unit["name"], "log"),
                        }
                        for unit in units
                    ],
                    "missing_metric_units": [],
                },
                "blocking_errors": blocking_errors,
            }

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
            host_detail["units"] = sorted(self._collect_host_units(host_id), key=lambda item: item["unitName"])
            return host_detail

    def update_service_resources(
        self,
        name: str,
        *,
        child_service_type: str,
        platform_auto: bool | None = None,
        cpu: float | None = None,
        memory_gb: float | None = None,
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
                    if memory_gb is not None:
                        unit["memory"] = memory_gb
                        unit["memoryGB"] = memory_gb

        return self._get_updated_service_detail(name)

    def update_service_storage(
        self,
        name: str,
        *,
        child_service_type: str,
        platform_auto: bool | None = None,
        data_volume_size_gb: float | None = None,
        log_volume_size_gb: float | None = None,
    ) -> dict[str, Any]:
        """按子服务类型批量更新其下所有 unit 的 data/log 卷规格。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)

            for child_service in target_services:
                if platform_auto is not None:
                    child_service["platformAuto"] = platform_auto
                for unit in child_service.get("units", []):
                    storage = unit["storage"]
                    if data_volume_size_gb is not None:
                        storage["data"]["size"] = data_volume_size_gb
                        storage["data"]["sizeGB"] = data_volume_size_gb
                    if log_volume_size_gb is not None:
                        storage["log"]["size"] = log_volume_size_gb
                        storage["log"]["sizeGB"] = log_volume_size_gb

            self._refresh_platform_aggregates()

        return self._get_updated_service_detail(name)

    def create_service_image_upgrade_task(
        self,
        name: str,
        *,
        child_service_type: str,
        image: str,
        version: str | None = None,
        unit_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """创建镜像升级异步任务。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)
            target_units = self._select_target_units(target_services, unit_names)
            now = self._utcnow()
            task_id = self._next_task_id(
                action="service.image.upgrade",
                service_name=name,
                child_service_type=child_service_type,
            )
            selected_unit_names = [unit["name"] for unit in target_units]
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
                    "unitNames": selected_unit_names,
                },
            }
            self._tasks_by_id[task_id] = task
        self._start_task_worker(task_id)
        return self._public_task(task)

    def describe_image_upgrade_capabilities(
        self,
        name: str,
        *,
        child_service_type: str,
    ) -> dict[str, Any]:
        """返回指定服务/子服务的可升级镜像和版本候选。"""

        with self._lock:
            target_services = self._get_target_child_services(name, child_service_type)
            current_version = self._child_service_base_version(target_services[0])
            available_targets = [
                {
                    "image": f"{child_service_type}:{version}",
                    "version": version,
                }
                for version in self._next_patch_versions(current_version)
            ]
            return {
                "supported": bool(available_targets),
                "availableTargets": available_targets,
            }

    def describe_backup_task_capabilities(
        self,
        *,
        service_type: str | None = None,
        service_name: str | None = None,
        unit_name: str | None = None,
    ) -> dict[str, Any]:
        """返回备份任务能力描述和轻量运行提示。"""

        with self._lock:
            resolved = self._resolve_backup_target(
                service_type=service_type,
                service_name=service_name,
                unit_name=unit_name,
                scope=None,
            )
            effective_service_type = resolved.get("serviceType") or service_type
            fields = self._backup_capability_fields(str(effective_service_type or "generic"))
            running = self._running_backups_for_resolved_target(resolved)
            result = {
                "supported": True,
                "serviceType": effective_service_type,
                "scopeValues": ["service", "unit"],
                "fields": fields,
            }
            if resolved:
                result["resolvedTarget"] = resolved
                result["runtimeHints"] = {
                    "backupRunning": bool(running),
                    "runningBackups": running,
                }
            return result

    def create_service_backup_task(
        self,
        name: str,
        *,
        scope: str,
        backup_type: str,
        retention_days: int,
        unit_name: str | None = None,
        options: dict[str, Any] | None = None,
        remark: str | None = None,
    ) -> dict[str, Any]:
        """创建手动备份异步任务并立即生成 running backup records。"""

        normalized_scope = (scope or "service").strip().lower()
        if normalized_scope not in {"service", "unit"}:
            raise ValueError("scope must be one of service, unit")

        with self._lock:
            service = self._services_by_name.get(name)
            if service is None:
                raise ServiceNotFoundError(name)
            resolved = self._resolve_backup_target(
                service_name=name,
                unit_name=unit_name,
                scope=normalized_scope,
            )
            target_units = self._backup_target_units(
                service,
                scope=normalized_scope,
                unit_name=unit_name,
            )
            now = self._utcnow()
            task_id = self._next_task_id(
                action="service.backup.create",
                service_name=name,
                child_service_type=normalized_scope,
            )
            task = {
                "taskId": task_id,
                "type": "service.backup.create",
                "status": "RUNNING",
                "message": "backup running",
                "reason": None,
                "resourceType": "service",
                "resourceName": name,
                "result": None,
                "createdAt": now,
                "updatedAt": now,
                "_operation": {
                    "kind": "service.backup.create",
                    "scope": normalized_scope,
                    "backupType": backup_type,
                    "retentionDays": retention_days,
                    "unitName": unit_name,
                    "options": deepcopy(options or {}),
                    "remark": remark,
                    "resolvedTarget": deepcopy(resolved),
                    "backupIds": [],
                },
            }
            compress_mode = str((options or {}).get("compressMode") or (options or {}).get("compress_mode") or "gzip")
            for index, item in enumerate(target_units, start=1):
                backup_id = f"backup-{self._slug(name)}-{self._slug(item['unit']['name'])}-{secrets.token_hex(3)}"
                task["_operation"]["backupIds"].append(backup_id)
                self._backups.append(
                    {
                        "backup_id": backup_id,
                        "task_id": task_id,
                        "service_name": name,
                        "service_type": service["type"],
                        "child_service_name": item["child_service"].get("name") or item["child_service"].get("type"),
                        "child_service_type": item["child_service"].get("type"),
                        "unit_name": item["unit"]["name"],
                        "backup_type": backup_type,
                        "backup_path": None,
                        "size_bytes": 0,
                        "storage_type": None,
                        "compress_mode": compress_mode,
                        "started_at": self._format_backup_time(self._utcnow_datetime()),
                        "finished_at": None,
                        "expires_at": None,
                        "duration_seconds": 0,
                        "task_status": "running",
                        "task_error": None,
                        "valid_status": "valid",
                        "remark": remark or f"手动备份 {index}/{len(target_units)}",
                        "owner_user": service.get("user"),
                        "deleted": False,
                    }
                )
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
                    "service_name": item["service_name"],
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
                        "service_name": item["service_name"],
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

        catalog_path = self.data_dir.parent.parent / "ai-agent" / "config" / "dbaas_metric_catalog.json"
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
            service_type = item.get("service_type")
            if not isinstance(service_type, str) or not service_type:
                raise DataValidationError(f"metric_key '{metric_key}' must define service_type")
            catalog_by_key[metric_key] = deepcopy(item)
        return catalog_by_key

    def _load_backups(self) -> list[dict[str, Any]]:
        """加载备份 seed 数据。"""

        backups = self._load_array_file(self.data_dir / "backups.json", resource_name="backups")
        normalized: list[dict[str, Any]] = []
        required_fields = {
            "backup_id",
            "task_id",
            "service_name",
            "service_type",
            "child_service_name",
            "child_service_type",
            "unit_name",
            "backup_type",
            "backup_path",
            "size_bytes",
            "storage_type",
            "compress_mode",
            "started_at",
            "finished_at",
            "expires_at",
            "duration_seconds",
            "task_status",
            "task_error",
            "valid_status",
            "remark",
        }
        seen_ids: set[str] = set()
        for item in backups:
            if not isinstance(item, dict):
                raise DataValidationError("backups.json items must be objects")
            backup_id = item.get("backup_id")
            if not isinstance(backup_id, str) or not backup_id:
                raise DataValidationError("each backup item must have a non-empty 'backup_id'")
            if backup_id in seen_ids:
                raise DataValidationError(f"duplicate backup_id '{backup_id}' in backups.json")
            missing = [field for field in required_fields if field not in item]
            if missing:
                raise DataValidationError(
                    f"backup_id '{backup_id}' is missing required fields: {', '.join(sorted(missing))}"
                )
            seen_ids.add(backup_id)
            normalized.append(deepcopy(item))
        return normalized

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

        normalized_service.setdefault("runningStatus", "passing")
        normalized_service["healthStatus"] = self._internal_health_status(normalized_service.get("runningStatus"))
        normalized_service.setdefault("subsystem", self._derive_subsystem(normalized_service))
        normalized_service.setdefault("ownerAccount", None)
        normalized_service.setdefault("ownerName", None)
        normalized_service.setdefault("businessSystemName", normalized_service.get("subsystem"))
        normalized_service.setdefault("businessSubsystemName", normalized_service.get("subsystem"))
        normalized_service.setdefault("areaName", None)
        normalized_service.setdefault("replicationStatus", normalized_service.get("runningStatus"))

        child_services = normalized_service.pop("childServices", None)
        services = child_services if child_services is not None else normalized_service.get("services")
        if not isinstance(services, list):
            raise DataValidationError(
                f"service '{normalized_service['name']}' must contain a 'childServices' list"
            )
        normalized_service["services"] = services

        child_type_counts: dict[str, int] = {}
        for child_service in services:
            if not isinstance(child_service, dict):
                raise DataValidationError(f"service '{normalized_service['name']}' child services must be objects")
            child_service_type = child_service.get("type")
            if not isinstance(child_service_type, str) or not child_service_type:
                raise DataValidationError(
                    f"child service in service '{normalized_service['name']}' must have a non-empty 'type'"
                )
            child_type_counts[child_service_type] = child_type_counts.get(child_service_type, 0) + 1
            child_service_name = child_service.get("name")
            if not isinstance(child_service_name, str) or not child_service_name or child_service_name == child_service_type:
                child_service["name"] = self._child_service_public_name(
                    normalized_service["name"],
                    child_service_type,
                    child_type_counts[child_service_type],
                )
            child_service.setdefault("runningStatus", "passing")
            child_service["healthStatus"] = self._internal_health_status(child_service.get("runningStatus"))
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
                host_id = self._resolve_unit_host_id(unit, service_name=normalized_service["name"])
                unit["hostId"] = host_id
                unit.setdefault("runningStatus", "passing")
                unit["healthStatus"] = self._internal_health_status(unit.get("runningStatus"))
                unit["containerStatus"] = self._internal_container_status(unit["healthStatus"])
                unit.setdefault("type", child_service.get("type") or "unit")
                unit.setdefault("ip", unit.get("containerIp"))
                unit["containerIp"] = unit.get("ip")
                unit["memory"] = unit.get("memoryGB", unit.get("memory"))
                unit["storage"] = self._normalize_unit_storage_seed(unit.get("storage"), host_id=host_id)

        return normalized_service

    def _child_service_public_name(self, service_name: str, child_service_type: str, occurrence: int) -> str:
        """返回稳定的子服务名称。"""

        return f"{service_name}-{child_service_type}-{occurrence:02d}"

    def _public_backup_record(self, backup: dict[str, Any]) -> dict[str, Any]:
        """返回对外可见的备份记录。"""

        public_fields = {
            "backup_id",
            "task_id",
            "service_name",
            "service_type",
            "child_service_name",
            "child_service_type",
            "unit_name",
            "backup_type",
            "size_bytes",
            "storage_type",
            "compress_mode",
            "started_at",
            "finished_at",
            "expires_at",
            "duration_seconds",
            "task_status",
            "task_error",
            "valid_status",
            "remark",
        }
        return {
            key: deepcopy(value)
            for key, value in backup.items()
            if key in public_fields
        }

    def _backup_owner_user(self, backup: dict[str, Any]) -> str | None:
        """返回备份记录所属用户。"""

        owner_user = backup.get("owner_user")
        if isinstance(owner_user, str) and owner_user:
            return owner_user
        service_name = backup.get("service_name")
        if not isinstance(service_name, str) or not service_name:
            return None
        service = self._services_by_name.get(service_name)
        if service is None:
            return None
        user = service.get("user")
        return user if isinstance(user, str) and user else None

    def _normalize_unit_storage_seed(self, storage: Any, *, host_id: str) -> dict[str, Any]:
        """规范化 seed 中的 unit 存储结构。"""

        if not isinstance(storage, dict):
            raise DataValidationError("unit storage must be an object with 'data' and 'log'")

        if "data" not in storage or "log" not in storage:
            raise DataValidationError("unit storage must contain 'data' and 'log' volumes")
        return {
            "data": self._normalize_volume_seed(
                storage["data"],
                volume_name="data",
                host_id=host_id,
            ),
            "log": self._normalize_volume_seed(
                storage["log"],
                volume_name="log",
                host_id=host_id,
            ),
        }

    def _normalize_volume_seed(self, volume: Any, *, volume_name: str, host_id: str) -> dict[str, Any]:
        """规范化 seed 中的 volume 结构。"""

        if not isinstance(volume, dict):
            raise DataValidationError(f"unit storage volume '{volume_name}' must be an object")
        host = self._hosts_by_id[host_id]
        disk_id = volume.get("diskId") or self._select_volume_disk_id(host, volume_name=volume_name, volume=volume)
        mount_point = volume.get("mountPoint") or f"/dbaas/{volume_name}"
        size = volume.get("sizeGB", volume.get("size"))
        if not isinstance(disk_id, str) or not disk_id:
            raise DataValidationError(f"unit storage volume '{volume_name}' must have a non-empty 'diskId'")
        if size is None:
            raise DataValidationError(f"unit storage volume '{volume_name}' must have 'sizeGB'")
        return {
            "diskId": disk_id,
            "mountPoint": mount_point,
            "size": float(size),
            "sizeGB": float(size),
            "type": volume.get("type"),
            "typeDisplayName": volume.get("typeDisplayName"),
        }

    def _resolve_unit_host_id(self, unit: dict[str, Any], *, service_name: str) -> str:
        """Resolve a public service unit back to a host id for in-memory platform behavior."""

        host_id = unit.get("hostId")
        if isinstance(host_id, str) and host_id:
            return host_id
        host_name = unit.get("hostName")
        host_ip = unit.get("hostIp")
        for candidate_id, host in self._hosts_by_id.items():
            if isinstance(host_name, str) and host_name and host.get("name") == host_name:
                return candidate_id
            if isinstance(host_ip, str) and host_ip and host.get("ip") == host_ip:
                return candidate_id
        raise DataValidationError(
            f"unit '{unit.get('name')}' in service '{service_name}' must have a resolvable hostName or hostIp"
        )

    def _select_volume_disk_id(self, host: dict[str, Any], *, volume_name: str, volume: dict[str, Any]) -> str:
        """Resolve a public volume to a host disk for in-memory capacity accounting."""

        requested_type = str(volume.get("type") or "")
        requested_media = requested_type.split(":", 1)[1] if ":" in requested_type else ""
        candidates = [
            disk
            for disk in host.get("disks", [])
            if disk.get("type") == volume_name or (volume_name == "log" and disk.get("type") == "data")
        ]
        if requested_media:
            media_matches = [disk for disk in candidates if str(disk.get("mediaType", "")).lower() == requested_media.lower()]
            if media_matches:
                return str(media_matches[0]["diskId"])
        if candidates:
            return str(candidates[0]["diskId"])
        disks = host.get("disks", [])
        if disks:
            return str(disks[0]["diskId"])
        raise DataValidationError(f"host '{host.get('id')}' has no disks for volume '{volume_name}'")

    def _internal_health_status(self, value: Any) -> str:
        """Map public running status values to internal platform health status values."""

        normalized = str(value or "").strip().lower()
        if normalized in {"passing", "healthy", "running", "success"}:
            return "HEALTHY"
        if normalized in {"warning", "warn", "degraded", "restarting", "maintenance"}:
            return "WARN"
        if normalized in {"critical", "unhealthy", "failed", "failure", "stopped"}:
            return "UNHEALTHY"
        return "HEALTHY"

    def _internal_container_status(self, health_status: str) -> str:
        """Return a simple container status for platform and precheck summaries."""

        if health_status == "HEALTHY":
            return "RUNNING"
        if health_status == "WARN":
            return "RESTARTING"
        return "FAILED"

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
                        size = float(volume.get("sizeGB", volume.get("size")) or 0.0)
                        disk["used"] = min(float(disk["capacity"]), float(disk["used"]) + size)
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
        public_service = {
            "name": service["name"],
            "type": service["type"],
            "user": service.get("user"),
            "ownerAccount": service.get("ownerAccount"),
            "ownerName": service.get("ownerName"),
            "businessSystemName": service.get("businessSystemName"),
            "businessSubsystemName": service.get("businessSubsystemName"),
            "subsystem": service.get("businessSubsystemName") or service.get("subsystem"),
            "siteId": service["siteId"],
            "siteName": service.get("siteName") or site["name"],
            "areaName": service.get("areaName") or site.get("areaName"),
            "sharding": service.get("sharding"),
            "runningStatus": self._public_running_status(service.get("runningStatus") or service.get("healthStatus")),
            "replicationStatus": self._public_running_status(service.get("replicationStatus")),
            "childServices": [],
            "backupStrategy": service.get("backupStrategy"),
        }

        for child_service in service.get("services", []):
            public_service["childServices"].append(
                {
                    "name": child_service["name"],
                    "type": child_service["type"],
                    "version": child_service.get("version"),
                    "port": child_service.get("port"),
                    "runningStatus": self._public_running_status(
                        child_service.get("runningStatus") or child_service.get("healthStatus")
                    ),
                    "units": [self._public_service_unit(unit, service) for unit in child_service.get("units", [])],
                }
            )

        return public_service

    def _public_service_detail_for_user(self, service: dict[str, Any]) -> dict[str, Any]:
        """返回普通用户可见的服务组详情。"""

        public_service = self._public_service_detail(service)
        public_service.pop("siteId", None)
        public_service.pop("ownerAccount", None)
        public_service.pop("ownerName", None)

        for child_service in public_service.get("childServices", []):
            for unit in child_service.get("units", []):
                unit.pop("hostName", None)
                unit.pop("hostIp", None)

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
            "subsystems": sorted(
                {
                    service.get("businessSubsystemName") or service["subsystem"]
                    for service in user_services
                }
            ),
        }

    def _public_user_detail(self, user: str, user_services: list[dict[str, Any]]) -> dict[str, Any]:
        """返回用户详情。"""

        user_detail = self._public_user_summary(user)
        user_detail["serviceGroups"] = [
            {
                "name": service["name"],
                "type": service["type"],
                "user": service.get("user"),
                "subsystem": service.get("businessSubsystemName") or service["subsystem"],
                "healthStatus": service["healthStatus"],
            }
            for service in user_services
        ]
        return user_detail

    def _public_unit_storage(self, storage: dict[str, Any], disk_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """把 seed 里的简化 volume 信息投影成服务视图存储结构。"""

        public_storage: dict[str, Any] = {}
        for volume_name in ("data", "log"):
            volume = deepcopy(storage[volume_name])
            disk = disk_by_id.get(volume.get("diskId", ""))
            public_storage[volume_name] = {
                "sizeGB": volume.get("sizeGB", volume.get("size")),
                "type": volume.get("type") or (f"local:{disk['mediaType']}" if disk is not None else None),
                "typeDisplayName": volume.get("typeDisplayName") or (self._disk_type_display_name(disk) if disk is not None else None),
            }
        return public_storage

    def _public_service_unit(self, unit: dict[str, Any], service: dict[str, Any]) -> dict[str, Any]:
        """返回服务视图中的单元信息。"""

        host = self._hosts_by_id[unit["hostId"]]
        return {
            "name": unit["name"],
            "type": unit["type"],
            "cpuArchitecture": unit.get("cpuArchitecture"),
            "cpuArchitectureDisplayName": unit.get("cpuArchitectureDisplayName"),
            "version": unit.get("version"),
            "runningStatus": self._public_running_status(unit.get("runningStatus") or unit.get("healthStatus")),
            "hostName": host["name"],
            "hostIp": host["ip"],
            "ip": unit.get("ip") or unit.get("containerIp"),
            "ipv6": unit.get("ipv6"),
            "cpu": unit.get("cpu"),
            "memoryGB": unit.get("memoryGB", unit.get("memory")),
            "storage": self._public_unit_storage(unit["storage"], host["_diskById"]),
        }

    def _public_running_status(self, value: Any) -> str | None:
        """把内部状态值转换为服务视图状态。"""

        if value is None:
            return None
        text = str(value)
        if text == "HEALTHY":
            return "passing"
        if text == "WARN":
            return "warning"
        if text == "UNHEALTHY":
            return "critical"
        return text

    def _disk_type_display_name(self, disk: dict[str, Any]) -> str:
        """返回磁盘类型展示名。"""

        media_type = disk.get("mediaType")
        if media_type == "SSD":
            return "本地固态盘"
        if media_type == "HDD":
            return "本地机械盘"
        return str(media_type or "未知磁盘")

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
            "unitName": unit["name"],
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

        metric_service_type = metric["service_type"]
        items: list[dict[str, Any]] = []
        for service in services:
            for child_service in service.get("services", []):
                service_type = child_service["type"]
                if metric_service_type != "container" and service_type != metric_service_type:
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

        metric_service_type = metric["service_type"]
        if metric_service_type == "container":
            return matches[0]
        for item in matches:
            if item["service_type"] == metric_service_type:
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

        metric_service_type = metric["service_type"]
        if service_name is not None:
            service = self._services_by_name.get(service_name)
            if service is None:
                raise ServiceNotFoundError(service_name)
            child_service_types = [
                child_service["type"]
                for child_service in service.get("services", [])
                if metric_service_type == "container" or child_service["type"] == metric_service_type
            ]
            if child_service_types:
                return child_service_types
            if metric_service_type == "container":
                return [service["type"]]
            return [metric_service_type]
        if owner_user is not None:
            child_service_types = [
                child_service["type"]
                for service in self._services_by_name.values()
                if service.get("user") == owner_user
                for child_service in service.get("services", [])
                if metric_service_type == "container" or child_service["type"] == metric_service_type
            ]
            if child_service_types:
                return sorted(set(child_service_types))
            if metric_service_type == "container":
                return ["mysql", "redis", "proxy"]
            return [metric_service_type]
        if metric_service_type == "container":
            return ["mysql", "redis", "proxy", "tidb", "tikv", "pd"]
        return [metric_service_type]

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
            "service_name": service_name or self._fake_service_name(owner_user, fake_index),
            "service_type": service_type,
            "unit_name": unit_name,
            "unit": {
                "name": unit_name,
                "version": self._version_for(service_type, fake_index),
                "memory": memory,
                "cpu": float((fake_index % 16) + 1),
            },
        }

    def _fake_service_name(self, owner_user: str | None, fake_index: int) -> str:
        """返回伪造单元所属的服务组名称。"""

        if owner_user is None:
            return f"mock-svc-{fake_index % 10_000:05d}"
        service_names = [
            service["name"]
            for service in sorted(self._services_by_name.values(), key=lambda item: item["name"])
            if service.get("user") == owner_user
        ]
        if not service_names:
            return f"{owner_user}-mock-svc"
        return service_names[fake_index % len(service_names)]

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

    def _child_service_base_version(self, child_service: dict[str, Any]) -> str:
        """返回子服务当前三段版本。"""

        version = child_service.get("version")
        if isinstance(version, str) and version:
            return version
        for unit in child_service.get("units", []):
            unit_version = unit.get("version")
            if isinstance(unit_version, str) and unit_version:
                return ".".join(unit_version.split(".")[:3])
        return "1.0.0"

    def _next_patch_versions(self, current_version: str) -> list[str]:
        """基于当前版本生成两个稳定的 patch 升级候选。"""

        parts = current_version.split(".")
        if len(parts) < 3:
            return []
        try:
            major = int(parts[0])
            minor = int(parts[1])
            patch = int(parts[2])
        except ValueError:
            return []
        return [
            f"{major}.{minor}.{patch + offset}"
            for offset in (1, 2)
        ]

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

    def _resolve_backup_target(
        self,
        *,
        service_type: str | None = None,
        service_name: str | None = None,
        unit_name: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """按全局唯一名称解析备份目标。"""

        if unit_name:
            service, child_service, unit = self._find_unit_by_name(unit_name)
            if service_name is not None and service["name"] != service_name:
                raise ServiceUnitNotFoundError(unit_name)
            return {
                "scope": "unit",
                "serviceName": service["name"],
                "serviceType": service["type"],
                "childServiceType": child_service.get("type"),
                "childServiceName": child_service.get("name"),
                "unitName": unit["name"],
            }

        if service_name:
            service = self._services_by_name.get(service_name)
            if service is None:
                raise ServiceNotFoundError(service_name)
            resolved: dict[str, Any] = {
                "scope": scope or "service",
                "serviceName": service["name"],
                "serviceType": service["type"],
            }
            return resolved

        if service_type:
            return {
                "scope": "service_type",
                "serviceType": service_type,
            }

        return {}

    def _backup_target_units(
        self,
        service: dict[str, Any],
        *,
        scope: str,
        unit_name: str | None,
    ) -> list[dict[str, dict[str, Any]]]:
        """返回备份任务对应的 child service / unit 列表。"""

        items: list[dict[str, dict[str, Any]]] = []
        for child_service in service.get("services", []):
            for unit in child_service.get("units", []):
                if scope == "unit" and unit.get("name") != unit_name:
                    continue
                items.append({"child_service": child_service, "unit": unit})

        if not items:
            if scope == "unit":
                raise ServiceUnitNotFoundError(unit_name or "")
        return items

    def _find_unit_by_name(self, unit_name: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        for service in self._services_by_name.values():
            for child_service in service.get("services", []):
                for unit in child_service.get("units", []):
                    if unit.get("name") == unit_name:
                        return service, child_service, unit
        raise ServiceUnitNotFoundError(unit_name)

    def _backup_capability_fields(self, service_type: str) -> list[dict[str, Any]]:
        fields = [
            {
                "name": "scope",
                "type": "string",
                "required": True,
                "enumValues": ["service", "unit"],
                "description": "备份范围",
                "requiresUserInput": True,
            },
            {
                "name": "backupType",
                "type": "string",
                "required": True,
                "enumValues": ["full"],
                "description": "备份类型",
                "requiresUserInput": True,
            },
            {
                "name": "retentionDays",
                "type": "integer",
                "required": True,
                "min": 1,
                "max": 365,
                "description": "备份保留天数",
                "requiresUserInput": True,
            },
            {
                "name": "remark",
                "type": "string",
                "required": False,
                "description": "备注",
                "requiresUserInput": False,
            },
        ]
        if service_type in {"mysql", "tidb"}:
            fields.append(
                {
                    "name": "options.compressMode",
                    "type": "string",
                    "required": False,
                    "enumValues": ["gzip", "none"],
                    "description": "压缩模式",
                    "requiresUserInput": True,
                }
            )
        if service_type == "redis":
            fields.append(
                {
                    "name": "options.rdbOnly",
                    "type": "boolean",
                    "required": False,
                    "description": "是否仅创建 RDB 备份",
                    "requiresUserInput": True,
                }
            )
        return fields

    def _running_backups_for_resolved_target(self, resolved: dict[str, Any]) -> list[dict[str, Any]]:
        if not resolved or resolved.get("scope") == "service_type":
            return []
        running: list[dict[str, Any]] = []
        for backup in self._backups:
            if backup.get("task_status") != "running":
                continue
            if not self._backup_matches_resolved_target(backup, resolved):
                continue
            running.append(
                {
                    "backupId": backup.get("backup_id"),
                    "taskId": backup.get("task_id"),
                    "serviceName": backup.get("service_name"),
                    "childServiceName": backup.get("child_service_name"),
                    "unitName": backup.get("unit_name"),
                    "startedAt": backup.get("started_at"),
                }
            )
        return running[:20]

    def _backup_matches_resolved_target(self, backup: dict[str, Any], resolved: dict[str, Any]) -> bool:
        service_name = resolved.get("serviceName")
        if service_name and backup.get("service_name") != service_name:
            return False
        unit_name = resolved.get("unitName")
        if unit_name:
            return backup.get("unit_name") == unit_name
        child_service_name = resolved.get("childServiceName")
        if child_service_name:
            return backup.get("child_service_name") == child_service_name
        child_service_type = resolved.get("childServiceType")
        if child_service_type:
            return backup.get("child_service_type") == child_service_type
        return True

    def _format_backup_time(self, value: datetime) -> str:
        """返回 Phase9 backups 使用的时间格式。"""

        return value.astimezone(UTC).replace(tzinfo=None, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def _get_updated_service_detail(self, name: str) -> dict[str, Any]:
        """返回更新后的完整服务详情。"""

        updated_service = self.get_service_detail(name)
        if updated_service is None:
            raise ServiceNotFoundError(name)
        return updated_service

    def _precheck_runtime(self, units: list[dict[str, Any]]) -> dict[str, Any]:
        """返回预检使用的运行状态摘要。"""

        abnormal_units = [
            {
                "unit_name": unit["name"],
                "status": str(unit.get("containerStatus") or unit.get("healthStatus") or "UNKNOWN").lower(),
            }
            for unit in units
            if unit.get("containerStatus") != "RUNNING"
        ]
        return {
            "unit_count": len(units),
            "running_count": sum(1 for unit in units if unit.get("containerStatus") == "RUNNING"),
            "abnormal_units": abnormal_units,
        }

    def _available_resource_specs(self, current_cpu: float, current_memory: float) -> list[dict[str, Any]]:
        """返回稳定的 mock 资源规格候选。"""

        candidates = {
            (max(current_cpu, 2.0), max(current_memory, 4.0)),
            (max(current_cpu * 2, 4.0), max(current_memory * 2, 8.0)),
            (max(current_cpu * 4, 8.0), max(current_memory * 4, 16.0)),
        }
        return [
            {
                "cpu_cores": cpu,
                "memory_gb": memory,
                "label": f"{self._format_number(cpu)}C{self._format_number(memory)}G",
            }
            for cpu, memory in sorted(candidates)
        ]

    def _resource_metric_stats(self, metric_name: str, unit_name: str) -> dict[str, str]:
        """返回稳定的 CPU/内存使用率摘要。"""

        latest_value = self._percent_number("resource", metric_name, unit_name, "latest", minimum=25, spread=65)
        max_value = min(99.9, latest_value + self._percent_number("resource", metric_name, unit_name, "max", minimum=5, spread=15))
        min_value = max(0.0, latest_value - self._percent_number("resource", metric_name, unit_name, "min", minimum=10, spread=20))
        avg_value = (latest_value + min_value + max_value) / 3
        return {
            "latest": self._format_percent(latest_value),
            "max": self._format_percent(max_value),
            "min": self._format_percent(min_value),
            "avg": self._format_percent(avg_value),
        }

    def _current_storage_spec(self, units: list[dict[str, Any]]) -> dict[str, float]:
        """返回当前 data/log 卷容量。"""

        if not units:
            return {"data_volume_gb": 0.0, "log_volume_gb": 0.0}
        storage = units[0]["storage"]
        return {
            "data_volume_gb": float(storage["data"].get("sizeGB", storage["data"].get("size"))),
            "log_volume_gb": float(storage["log"].get("sizeGB", storage["log"].get("size"))),
        }

    def _resource_capacity_errors(
        self,
        units: list[dict[str, Any]],
        *,
        target_cpu_cores: float | None,
        target_memory_gb: float | None,
    ) -> list[dict[str, str]]:
        """校验目标 CPU/内存是否超过所在主机容量。"""

        if target_cpu_cores is None and target_memory_gb is None:
            return []
        if (
            target_cpu_cores is not None
            and target_cpu_cores > MOCK_RESOURCE_CPU_CAPACITY_LIMIT
        ) or (
            target_memory_gb is not None
            and target_memory_gb > MOCK_RESOURCE_MEMORY_CAPACITY_LIMIT_GB
        ):
            return [
                {
                    "code": "insufficient_capacity",
                    "message": "当前主机或资源池资源不足，无法调整到目标值。",
                }
            ]

        host_usage = self._host_resource_usage()
        for unit in units:
            host = self._hosts_by_id[unit["hostId"]]
            usage = host_usage[unit["hostId"]]
            next_cpu = usage["cpu"] - float(unit.get("cpu") or 0.0) + float(target_cpu_cores or unit.get("cpu") or 0.0)
            next_memory = usage["memory"] - float(unit.get("memory") or 0.0) + float(target_memory_gb or unit.get("memory") or 0.0)
            if next_cpu > float(host["cpuCapacity"]) or next_memory > float(host["memoryCapacity"]):
                return [
                    {
                        "code": "insufficient_capacity",
                        "message": "当前主机或资源池资源不足，无法调整到目标值。",
                    }
                ]
        return []

    def _storage_capacity_errors(
        self,
        units: list[dict[str, Any]],
        *,
        target_data_volume_gb: float | None,
        target_log_volume_gb: float | None,
    ) -> list[dict[str, str]]:
        """校验目标 data/log 容量是否超过所在磁盘容量。"""

        if target_data_volume_gb is None and target_log_volume_gb is None:
            return []
        if (
            target_data_volume_gb is not None
            and target_data_volume_gb > MOCK_STORAGE_CAPACITY_LIMIT_GB
        ) or (
            target_log_volume_gb is not None
            and target_log_volume_gb > MOCK_STORAGE_CAPACITY_LIMIT_GB
        ):
            return [
                {
                    "code": "insufficient_capacity",
                    "message": "当前存储池资源不足，无法调整到目标值。",
                }
            ]

        for unit in units:
            host = self._hosts_by_id[unit["hostId"]]
            for volume_name, target_size in (
                ("data", target_data_volume_gb),
                ("log", target_log_volume_gb),
            ):
                if target_size is None:
                    continue
                volume = unit["storage"][volume_name]
                disk = host["_diskById"][volume["diskId"]]
                current_size = float(volume.get("sizeGB", volume.get("size")) or 0.0)
                next_used = float(disk["used"]) - current_size + float(target_size)
                if next_used > float(disk["capacity"]):
                    return [
                        {
                            "code": "insufficient_capacity",
                            "message": "当前存储池资源不足，无法调整到目标值。",
                        }
                    ]
        return []

    def _host_resource_usage(self) -> dict[str, dict[str, float]]:
        """汇总当前每台主机已分配的 CPU/内存。"""

        usage = {host_id: {"cpu": 0.0, "memory": 0.0} for host_id in self._hosts_by_id}
        for service in self._services_by_name.values():
            for child_service in service.get("services", []):
                for unit in child_service.get("units", []):
                    host_usage = usage[unit["hostId"]]
                    host_usage["cpu"] += float(unit.get("cpu") or 0.0)
                    host_usage["memory"] += float(unit.get("memory") or 0.0)
        return usage

    def _percent_metric(self, *parts: object) -> str:
        """返回稳定的百分比字符串。"""

        return self._format_percent(self._percent_number(*parts, minimum=15, spread=80))

    def _percent_number(self, *parts: object, minimum: float, spread: float) -> float:
        """返回稳定的百分比数字。"""

        seed = self._stable_int(*parts)
        return round(minimum + (seed % int(spread * 10)) / 10, 1)

    def _format_percent(self, value: float) -> str:
        """格式化百分比字符串。"""

        return f"{round(value, 1):.1f}%"

    def _format_number(self, value: float) -> str:
        """格式化规格数字。"""

        if float(value).is_integer():
            return str(int(value))
        return str(round(value, 1))

    def _select_target_units(self, target_services: list[dict[str, Any]], unit_names: list[str] | None) -> list[dict[str, Any]]:
        """返回本次任务要操作的单元列表。"""

        units = [unit for child_service in target_services for unit in child_service.get("units", [])]
        if unit_names is None:
            return units

        units_by_name = {unit["name"]: unit for unit in units}
        missing_unit_names = [unit_name for unit_name in unit_names if unit_name not in units_by_name]
        if missing_unit_names:
            raise ServiceUnitNotFoundError(", ".join(missing_unit_names))
        return [units_by_name[unit_name] for unit_name in unit_names]

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
            if task_type == "service.backup.create":
                self._run_service_backup_task(task_id, operation)
                return

            raise ValueError(f"unsupported task type '{task_type}'")
        except Exception as error:  # noqa: BLE001
            self._mark_task_failed(task_id, str(error))

    def _run_service_image_upgrade_task(self, task_id: str, operation: dict[str, Any]) -> None:
        """后台逐个执行镜像升级任务。"""

        for unit_name in operation["unitNames"]:
            time.sleep(self.task_unit_interval_seconds)
            with self._lock:
                task = self._get_task_for_update(task_id)
                unit = self._get_unit_by_name(
                    task["resourceName"],
                    operation["childServiceType"],
                    unit_name,
                )
                unit["image"] = operation["image"]
                if operation["version"] is not None:
                    unit["version"] = self._unit_upgrade_version(
                        operation["version"],
                        current_version=unit.get("version"),
                    )
                task["message"] = "image upgrade running"
                task["updatedAt"] = self._utcnow()

        with self._lock:
            task = self._get_task_for_update(task_id)
            task["status"] = "SUCCESS"
            task["message"] = "image upgrade completed"
            task["updatedAt"] = self._utcnow()
            task["result"] = {
                "childServiceType": operation["childServiceType"],
                "unitNames": operation["unitNames"],
                "image": operation["image"],
                "version": operation["version"],
            }

    def _unit_upgrade_version(self, target_version: str, *, current_version: Any) -> str:
        """把三段目标版本转换为 unit 使用的四段版本。"""

        parts = target_version.split(".")
        if len(parts) >= 4:
            return ".".join(parts[:4])

        current_parts = str(current_version or "").split(".")
        suffix = current_parts[3] if len(current_parts) >= 4 and current_parts[3] else "1"
        padded = [*parts, *("0" for _ in range(max(0, 3 - len(parts))))]
        return ".".join([*padded[:3], suffix])

    def _run_service_backup_task(self, task_id: str, operation: dict[str, Any]) -> None:
        """后台完成手动备份任务，并更新对应 backup records。"""

        backup_ids = operation.get("backupIds") or []
        if not isinstance(backup_ids, list):
            backup_ids = []
        time.sleep(max(self.task_unit_interval_seconds, 0.01))
        with self._lock:
            finished_dt = self._utcnow_datetime()
            finished_at = self._format_backup_time(finished_dt)
            retention_days = int(operation.get("retentionDays") or 0)
            expires_at = self._format_backup_time(finished_dt + timedelta(days=retention_days))
            for backup in self._backups:
                if backup.get("task_id") != task_id:
                    continue
                backup["task_status"] = "succeeded"
                backup["task_error"] = None
                backup["finished_at"] = finished_at
                backup["duration_seconds"] = 1
                backup["expires_at"] = expires_at
                backup["backup_path"] = f"mock://backups/{task_id}/{backup['backup_id']}.bak"
                backup["storage_type"] = "NAS"
                backup["size_bytes"] = max(1024, self._stable_int(task_id, backup["backup_id"]) % 10_000_000)
            task = self._get_task_for_update(task_id)
            task["status"] = "SUCCESS"
            task["message"] = "backup completed"
            task["updatedAt"] = self._utcnow()
            task["result"] = {
                "scope": operation.get("scope"),
                "backupType": operation.get("backupType"),
                "retentionDays": operation.get("retentionDays"),
                "backupIds": backup_ids,
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
            for backup in self._backups:
                if backup.get("task_id") != task_id:
                    continue
                backup["task_status"] = "failed"
                backup["task_error"] = reason
                backup["finished_at"] = self._format_backup_time(self._utcnow_datetime())
                backup["duration_seconds"] = 1

    def _get_task_for_update(self, task_id: str) -> dict[str, Any]:
        """返回可写的任务对象。"""

        task = self._tasks_by_id.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _get_unit_by_name(self, name: str, child_service_type: str, unit_name: str) -> dict[str, Any]:
        """返回指定子服务中的目标单元。"""

        target_services = self._get_target_child_services(name, child_service_type)
        for child_service in target_services:
            for unit in child_service.get("units", []):
                if unit.get("name") == unit_name:
                    return unit
        raise ServiceUnitNotFoundError(unit_name)

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

    def _next_task_id(
        self,
        *,
        action: str,
        service_name: str,
        child_service_type: str,
    ) -> str:
        """生成包含动作、服务、子服务和随机后缀的任务 ID。"""

        prefix = "-".join(
            [
                "task",
                self._slug(action),
                self._slug(service_name),
                self._slug(child_service_type),
            ]
        )
        while True:
            task_id = f"{prefix}-{secrets.token_hex(3)}"
            if task_id not in self._tasks_by_id:
                return task_id

    @staticmethod
    def _slug(value: str) -> str:
        """将任务 ID 组成片段规范化为 URL 友好的短文本。"""

        normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return normalized or "unknown"

    def _utcnow(self) -> str:
        """返回当前 UTC 时间字符串。"""

        return self._utcnow_datetime().isoformat().replace("+00:00", "Z")

    def _utcnow_datetime(self) -> datetime:
        """返回当前 UTC 时间。"""

        return datetime.now(UTC)

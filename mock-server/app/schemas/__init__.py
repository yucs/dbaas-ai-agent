"""HTTP 接口级 schema 定义。"""

from .platform import (
    ClusterDetailResponse,
    ClusterSummary,
    HostDetailResponse,
    HostDisk,
    HostSummary,
    HostUnitSummary,
    ServiceGroupSummary,
    SiteDetailResponse,
    SiteSummary,
)
from .service_detail import (
    BackupStrategySummary,
    ChildService,
    ServiceNetworkSpec,
    ServiceDetailResponse,
    ServiceImageUpgradeRequest,
    ServiceStorageSpec,
    ServiceUnit,
    ServiceVolumeSpec,
    UpdateServiceResourceRequest,
    UpdateServiceStorageRequest,
    UpdateStorageSpecRequest,
)
from .task import CreateTaskResponse, Task

__all__ = [
    "BackupStrategySummary",
    "ChildService",
    "ClusterDetailResponse",
    "ClusterSummary",
    "CreateTaskResponse",
    "HostDetailResponse",
    "HostDisk",
    "HostSummary",
    "HostUnitSummary",
    "ServiceGroupSummary",
    "ServiceDetailResponse",
    "ServiceImageUpgradeRequest",
    "ServiceNetworkSpec",
    "ServiceStorageSpec",
    "ServiceUnit",
    "ServiceVolumeSpec",
    "SiteDetailResponse",
    "SiteSummary",
    "Task",
    "UpdateServiceResourceRequest",
    "UpdateServiceStorageRequest",
    "UpdateStorageSpecRequest",
]

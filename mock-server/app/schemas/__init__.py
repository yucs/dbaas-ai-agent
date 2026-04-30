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
from .metric import HistoryMetricPoint, LatestMetricPoint
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
from .user import UserDetailResponse, UserSummary

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
    "HistoryMetricPoint",
    "LatestMetricPoint",
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
    "UserDetailResponse",
    "UserSummary",
    "UpdateServiceResourceRequest",
    "UpdateServiceStorageRequest",
    "UpdateStorageSpecRequest",
]

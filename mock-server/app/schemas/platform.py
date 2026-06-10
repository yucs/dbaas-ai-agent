"""站点、集群、主机相关 schema。"""

from pydantic import Field

from .service_detail import ApiSchema


class ServiceGroupSummary(ApiSchema):
    """服务组摘要。"""

    name: str = Field(description="服务组名称")
    type: str = Field(description="服务组类型")
    user: str | None = Field(default=None, description="服务组所属用户")
    subsystem: str = Field(description="服务组所属子系统")
    healthStatus: str = Field(description="服务组健康状态")


class SiteSummary(ApiSchema):
    """站点摘要。"""

    id: str = Field(description="站点唯一标识")
    name: str = Field(description="站点名称")
    environment: str = Field(description="站点所在环境")
    region: str = Field(description="站点所在区域")
    zone: str = Field(description="站点所在可用区")
    healthStatus: str = Field(description="站点健康状态")
    clusterCount: int = Field(description="站点下集群数量")
    hostCount: int = Field(description="站点下主机数量")
    serviceGroupCount: int = Field(description="站点下服务组数量")


class ClusterSummary(ApiSchema):
    """集群摘要。"""

    id: str = Field(description="集群唯一标识")
    name: str = Field(description="集群名称")
    siteId: str = Field(description="集群所属站点 ID")
    siteName: str = Field(description="集群所属站点名称")
    environment: str = Field(description="集群所在环境")
    region: str = Field(description="集群所在区域")
    zone: str = Field(description="集群所在可用区")
    clusterType: str = Field(description="集群类型，例如 KUBERNETES、BAREMETAL")
    scheduler: str = Field(description="集群调度器类型")
    healthStatus: str = Field(description="集群健康状态")
    hostCount: int = Field(description="集群下主机数量")
    unitCount: int = Field(description="集群下单元数量")
    serviceGroupCount: int = Field(description="集群相关服务组数量")


class HostStorageDevice(ApiSchema):
    """主机存储设备摘要。"""

    device: str = Field(description="主机侧设备路径")
    capacityGB: float = Field(description="存储总容量，单位 GB")
    usedGB: float = Field(description="已使用容量，单位 GB")
    availableGB: float = Field(description="可用容量，单位 GB")
    usagePercent: float = Field(description="容量使用率")


class HostUnitSummary(ApiSchema):
    """主机上的单元摘要。"""

    serviceName: str = Field(description="所属服务组名称")
    childServiceType: str = Field(description="所属子服务类型")
    unitName: str = Field(description="单元名称")
    containerIp: str = Field(description="容器 IP")
    healthStatus: str = Field(description="单元健康状态")
    containerStatus: str = Field(description="容器状态")


class HostSummary(ApiSchema):
    """主机摘要。"""

    id: str = Field(description="主机唯一标识")
    name: str = Field(description="主机名称")
    ip: str = Field(description="主机 IP")
    sshPort: int = Field(description="SSH 端口")
    siteId: str = Field(description="主机所属站点 ID")
    siteName: str = Field(description="主机所属站点名称")
    areaId: str = Field(description="主机所属区域 ID")
    areaName: str = Field(description="主机所属区域名称")
    clusterId: str = Field(description="主机所属集群 ID")
    clusterName: str = Field(description="主机所属集群名称")
    room: str = Field(description="主机所在机房")
    seat: str = Field(description="主机所在机位")
    networkPartition: str = Field(description="主机 HA 网络分区")
    status: str = Field(description="主机管控状态")
    healthStatus: str = Field(description="主机健康状态")
    cpuArchitecture: str = Field(description="CPU 架构")
    cpuArchitectureName: str = Field(description="CPU 架构显示名称")
    cpuCapacityCores: float = Field(description="CPU 总核数")
    cpuAllocatedCores: float = Field(description="已分配 CPU 核数")
    cpuAvailableCores: float = Field(description="可分配 CPU 核数")
    cpuAllocationPercent: float = Field(description="CPU 分配率")
    memoryCapacityGB: float = Field(description="内存总容量，单位 GB")
    memoryAllocatedGB: float = Field(description="已分配内存，单位 GB")
    memoryAvailableGB: float = Field(description="可分配内存，单位 GB")
    memoryAllocationPercent: float = Field(description="内存分配率")
    hdd: HostStorageDevice | None = Field(default=None, description="HDD 存储设备")
    ssd: HostStorageDevice | None = Field(default=None, description="SSD 存储设备")
    sanName: str | None = Field(default=None, description="SAN 存储名称")
    maxUnitCount: int = Field(description="主机最大承载单元数量")
    maxUsagePercent: float = Field(description="主机最大资源使用率")
    unitCount: int = Field(description="主机当前承载单元数量")
    createdAt: str = Field(description="主机记录创建时间")
    creator: str = Field(description="主机记录创建人账号")
    creatorName: str = Field(description="主机记录创建人姓名")


class SiteDetailResponse(SiteSummary):
    """站点详情。"""

    clusters: list[ClusterSummary] = Field(default_factory=list, description="站点下的集群列表")
    serviceGroups: list[ServiceGroupSummary] = Field(default_factory=list, description="站点下的服务组列表")


class ClusterDetailResponse(ClusterSummary):
    """集群详情。"""

    hosts: list[HostSummary] = Field(default_factory=list, description="集群下的主机列表")


class HostDetailResponse(HostSummary):
    """主机详情。"""

    units: list[HostUnitSummary] = Field(default_factory=list, description="主机上承载的单元列表")

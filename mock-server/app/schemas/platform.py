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


class HostDisk(ApiSchema):
    """主机磁盘信息。"""

    diskId: str = Field(description="磁盘唯一标识")
    name: str = Field(description="磁盘名称")
    type: str = Field(description="磁盘用途，例如 system、data、log")
    mediaType: str = Field(description="磁盘介质类型，例如 SSD、HDD")
    mountPoint: str = Field(description="主机侧挂载点")
    capacity: float = Field(description="磁盘总容量")
    used: float = Field(description="磁盘已使用容量")
    healthStatus: str = Field(description="磁盘健康状态")


class HostUnitSummary(ApiSchema):
    """主机上的单元摘要。"""

    serviceName: str = Field(description="所属服务组名称")
    childServiceType: str = Field(description="所属子服务类型")
    unitId: str = Field(description="单元唯一标识")
    unitName: str = Field(description="单元名称")
    role: str = Field(description="单元角色")
    containerIp: str = Field(description="容器 IP")
    healthStatus: str = Field(description="单元健康状态")
    containerStatus: str = Field(description="容器状态")


class HostSummary(ApiSchema):
    """主机摘要。"""

    id: str = Field(description="主机唯一标识")
    name: str = Field(description="主机名称")
    ip: str = Field(description="主机 IP")
    siteId: str = Field(description="主机所属站点 ID")
    siteName: str = Field(description="主机所属站点名称")
    clusterId: str = Field(description="主机所属集群 ID")
    clusterName: str = Field(description="主机所属集群名称")
    environment: str = Field(description="主机所在环境")
    region: str = Field(description="主机所在区域")
    zone: str = Field(description="主机所在可用区")
    hostStatus: str = Field(description="主机运行状态")
    healthStatus: str = Field(description="主机健康状态")
    cpuCapacity: float = Field(description="主机 CPU 总容量")
    memoryCapacity: float = Field(description="主机内存总容量")
    unitCount: int = Field(description="主机承载单元数量")
    disks: list[HostDisk] = Field(default_factory=list, description="主机磁盘列表")


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

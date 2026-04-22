"""服务详情接口 schema。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiSchema(BaseModel):
    """接口级 schema 的公共基类。"""

    model_config = ConfigDict(populate_by_name=True)


class ServiceVolumeSpec(ApiSchema):
    """单元 volume 规格。"""

    diskId: str = Field(description="挂载目标主机磁盘 ID")
    diskName: str = Field(description="挂载目标主机磁盘名称")
    diskType: str = Field(description="挂载目标主机磁盘用途，例如 data、log")
    mediaType: str = Field(description="挂载目标主机磁盘介质类型，例如 SSD、HDD")
    mountPoint: str = Field(description="容器内挂载路径")
    size: float = Field(description="卷容量大小")


class ServiceStorageSpec(ApiSchema):
    """单元存储规格。"""

    data: ServiceVolumeSpec = Field(description="data 卷规格")
    log: ServiceVolumeSpec = Field(description="log 卷规格")


class ServiceNetworkSpec(ApiSchema):
    """服务组网络信息。"""

    vpcId: str = Field(description="服务组所在 VPC ID")
    subnetId: str = Field(description="服务组所在子网 ID")
    cidr: str = Field(description="服务组所在子网网段")
    gateway: str = Field(description="服务组所在子网网关")


class ServiceUnit(ApiSchema):
    """子服务下的单元信息。"""

    id: str = Field(description="单元唯一标识")
    name: str = Field(description="单元名称")
    type: str = Field(description="单元类型，例如 docker")
    role: str = Field(description="单元角色，例如 primary、replica、proxy、manager")
    image: str | None = Field(default=None, description="单元容器镜像名称")
    version: str | None = Field(default=None, description="单元真实版本，例如 8.0.36")
    healthStatus: str = Field(description="单元健康状态，例如 HEALTHY、DEGRADED、UNHEALTHY")
    containerStatus: str = Field(description="单元容器状态，例如 RUNNING、STOPPED、RESTARTING")
    hostId: str = Field(description="单元所在主机 ID")
    hostName: str = Field(description="单元所在主机名称")
    hostIp: str = Field(description="单元所在主机 IP")
    containerIp: str = Field(description="单元容器 IP")
    cpu: float | None = Field(default=None, description="CPU 核数")
    memory: float | None = Field(default=None, description="内存大小")
    storage: ServiceStorageSpec = Field(description="单元存储规格")


class ChildService(ApiSchema):
    """服务组中的子服务信息。"""

    name: str = Field(description="子服务名称")
    type: str = Field(description="子服务类型")
    version: str | None = Field(default=None, description="子服务版本")
    port: int | None = Field(default=None, description="服务端口")
    healthStatus: str = Field(description="子服务健康状态，例如 HEALTHY、DEGRADED、UNHEALTHY")
    clusterHA: bool | None = Field(default=None, description="是否开启集群高可用")
    nodeHA: bool | None = Field(default=None, description="是否开启节点高可用")
    platformAuto: bool | None = Field(default=None, description="是否由平台自动分配规格")
    units: list[ServiceUnit] = Field(default_factory=list, description="子服务下的单元列表")


class UpdateStorageSpecRequest(ApiSchema):
    """存储更新请求。"""

    dataVolumeSize: float | None = Field(default=None, gt=0, description="更新后的 data 卷大小")
    logVolumeSize: float | None = Field(default=None, gt=0, description="更新后的 log 卷大小")

    @model_validator(mode="after")
    def validate_storage_fields(self) -> "UpdateStorageSpecRequest":
        """要求至少传入一个存储字段。"""

        if self.dataVolumeSize is None and self.logVolumeSize is None:
            raise ValueError("at least one storage field must be provided")
        return self


class UpdateServiceResourceRequest(ApiSchema):
    """`PUT /services/{name}/resource` 的请求模型。"""

    childServiceType: str = Field(description="目标子服务类型，例如 mysql、proxy")
    platformAuto: bool | None = Field(default=None, description="是否由平台自动分配规格")
    cpu: float | None = Field(default=None, gt=0, description="更新后的 CPU 核数")
    memory: float | None = Field(default=None, gt=0, description="更新后的内存大小")

    @model_validator(mode="after")
    def validate_resource_fields(self) -> "UpdateServiceResourceRequest":
        """要求至少传入一个资源字段。"""

        if self.platformAuto is None and self.cpu is None and self.memory is None:
            raise ValueError("at least one of 'platformAuto', 'cpu' or 'memory' must be provided")
        return self


class UpdateServiceStorageRequest(ApiSchema):
    """`PUT /services/{name}/storage` 的请求模型。"""

    childServiceType: str = Field(description="目标子服务类型，例如 mysql、proxy")
    platformAuto: bool | None = Field(default=None, description="是否由平台自动分配规格")
    storage: UpdateStorageSpecRequest | None = Field(default=None, description="更新后的存储规格")

    @model_validator(mode="after")
    def validate_storage_request_fields(self) -> "UpdateServiceStorageRequest":
        """要求至少传入 platformAuto 或 storage。"""

        if self.platformAuto is None and self.storage is None:
            raise ValueError("at least one of 'platformAuto' or 'storage' must be provided")
        return self


class ServiceImageUpgradeRequest(ApiSchema):
    """`POST /services/{name}/image-upgrade` 的请求模型。"""

    childServiceType: str = Field(description="目标子服务类型，例如 mysql、proxy")
    image: str = Field(description="目标镜像，例如 mysql:8.0.37")
    version: str | None = Field(default=None, description="目标版本号，例如 8.0.37")
    unitIds: list[str] | None = Field(
        default=None,
        description="指定升级的单元 ID 列表；不传时表示升级该子服务下所有单元",
    )


class BackupStrategySummary(ApiSchema):
    """服务组对应的备份策略摘要。"""

    enabled: bool = Field(description="是否启用备份")
    type: str | None = Field(default=None, description="备份类型")
    cronExpression: str | None = Field(default=None, description="备份 cron 表达式")
    retention: int | None = Field(default=None, description="备份保留天数")
    compressMode: str | None = Field(default=None, description="压缩模式")
    sendAlarm: bool | None = Field(default=None, description="是否发送告警")


class ServiceDetailResponse(ApiSchema):
    """`GET /services/{name}` 的响应模型。"""

    name: str = Field(description="服务组名称")
    type: str = Field(description="服务组类型")
    user: str | None = Field(default=None, description="服务组所属用户")
    subsystem: str = Field(description="服务组所属子系统")
    environment: str = Field(description="服务组所在环境，例如 prod、staging、dev、perf")
    siteId: str = Field(description="服务组所属站点 ID")
    siteName: str = Field(description="服务组所属站点名称")
    region: str = Field(description="服务组所在区域")
    zone: str = Field(description="服务组所在可用区")
    architecture: str | None = Field(default=None, description="服务组架构描述")
    sharding: bool | None = Field(default=None, description="是否为分片结构")
    healthStatus: str = Field(description="服务组健康状态，例如 HEALTHY、DEGRADED、UNHEALTHY")
    network: ServiceNetworkSpec = Field(description="服务组网络信息")
    services: list[ChildService] = Field(default_factory=list, description="服务组下的子服务列表")
    backupStrategy: BackupStrategySummary | None = Field(
        default=None,
        description="服务组备份策略摘要，运行时可由备份策略数据合并得到",
    )

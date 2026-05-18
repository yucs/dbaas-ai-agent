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


class BlockingError(ApiSchema):
    """预检发现的阻断错误。"""

    code: str = Field(description="错误码")
    message: str = Field(description="错误说明")


class PrecheckRuntimeUnit(ApiSchema):
    """预检返回的异常单元摘要。"""

    unit_name: str = Field(description="单元名称")
    status: str = Field(description="单元状态")


class PrecheckRuntime(ApiSchema):
    """预检返回的运行状态摘要。"""

    unit_count: int = Field(description="单元总数")
    running_count: int = Field(description="RUNNING 单元数")
    abnormal_units: list[PrecheckRuntimeUnit] = Field(default_factory=list, description="异常单元列表")


class PrecheckResourceSpec(ApiSchema):
    """CPU / 内存规格。"""

    cpu_cores: float = Field(description="CPU 核数")
    memory_gb: float = Field(description="内存大小")


class PrecheckAvailableResourceSpec(PrecheckResourceSpec):
    """DBAAS 支持选择的资源规格套餐。"""

    label: str = Field(description="规格展示标签")


class PrecheckResourceMetricStats(ApiSchema):
    """单项资源使用率摘要。"""

    latest: str = Field(description="最新值")
    max: str = Field(description="最近窗口最大值")
    min: str = Field(description="最近窗口最小值")
    avg: str = Field(description="最近窗口平均值")


class PrecheckResourceUnitMetric(ApiSchema):
    """单元 CPU / 内存使用率摘要。"""

    unit_name: str = Field(description="单元名称")
    cpu: PrecheckResourceMetricStats = Field(description="CPU 使用率")
    memory: PrecheckResourceMetricStats = Field(description="内存使用率")


class PrecheckResourceMetrics(ApiSchema):
    """资源规格预检监控摘要。"""

    time_window: str = Field(description="统计窗口")
    units: list[PrecheckResourceUnitMetric] = Field(default_factory=list, description="单元监控摘要")
    missing_metric_units: list[str] = Field(default_factory=list, description="缺失监控数据的单元")


class PrecheckServiceResourceUpdateRequest(ApiSchema):
    """资源规格调整预检请求。"""

    service_name: str = Field(description="服务名")
    child_service_type: str = Field(description="子服务类型")
    target_cpu_cores: float | None = Field(default=None, gt=0, description="目标 CPU 核数")
    target_memory_gb: float | None = Field(default=None, gt=0, description="目标内存大小")


class PrecheckServiceResourceUpdateResponse(ApiSchema):
    """资源规格调整预检响应。"""

    service_name: str = Field(description="服务名")
    child_service_type: str = Field(description="子服务类型")
    current_spec: PrecheckResourceSpec = Field(description="当前规格")
    available_specs: list[PrecheckAvailableResourceSpec] = Field(
        default_factory=list,
        description="DBAAS 支持选择的资源规格",
    )
    runtime: PrecheckRuntime = Field(description="运行状态摘要")
    metrics: PrecheckResourceMetrics = Field(description="监控摘要")
    blocking_errors: list[BlockingError] = Field(default_factory=list, description="阻断错误")


class PrecheckStorageSpec(ApiSchema):
    """data / log 卷容量。"""

    data_volume_gb: float = Field(description="data 卷容量")
    log_volume_gb: float = Field(description="log 卷容量")


class PrecheckStorageUnitMetric(ApiSchema):
    """单元 data / log 当前使用率。"""

    unit_name: str = Field(description="单元名称")
    data_usage: str = Field(description="data 卷当前使用率")
    log_usage: str = Field(description="log 卷当前使用率")


class PrecheckStorageMetrics(ApiSchema):
    """存储规格预检监控摘要。"""

    units: list[PrecheckStorageUnitMetric] = Field(default_factory=list, description="单元监控摘要")
    missing_metric_units: list[str] = Field(default_factory=list, description="缺失监控数据的单元")


class PrecheckServiceStorageUpdateRequest(ApiSchema):
    """存储规格调整预检请求。"""

    service_name: str = Field(description="服务名")
    child_service_type: str = Field(description="子服务类型")
    target_data_volume_gb: float | None = Field(default=None, gt=0, description="目标 data 卷容量")
    target_log_volume_gb: float | None = Field(default=None, gt=0, description="目标 log 卷容量")


class PrecheckServiceStorageUpdateResponse(ApiSchema):
    """存储规格调整预检响应。"""

    service_name: str = Field(description="服务名")
    child_service_type: str = Field(description="子服务类型")
    current_storage: PrecheckStorageSpec = Field(description="当前存储规格")
    runtime: PrecheckRuntime = Field(description="运行状态摘要")
    metrics: PrecheckStorageMetrics = Field(description="监控摘要")
    blocking_errors: list[BlockingError] = Field(default_factory=list, description="阻断错误")


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

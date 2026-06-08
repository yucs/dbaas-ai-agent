"""服务详情接口 schema。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiSchema(BaseModel):
    """接口级 schema 的公共基类。"""

    model_config = ConfigDict(populate_by_name=True)


class ServiceVolumeSpec(ApiSchema):
    """单元 volume 规格。"""

    sizeGB: float = Field(description="容量大小，单位 GB")
    type: str | None = Field(default=None, description="机器可读磁盘类型")
    typeDisplayName: str | None = Field(default=None, description="用户可读磁盘类型名称")


class UserServiceVolumeSpec(ApiSchema):
    """普通用户可见的单元 volume 规格。"""

    sizeGB: float = Field(description="容量大小，单位 GB")
    type: str | None = Field(default=None, description="机器可读磁盘类型")
    typeDisplayName: str | None = Field(default=None, description="用户可读磁盘类型名称")


class ServiceStorageSpec(ApiSchema):
    """单元存储规格。"""

    data: ServiceVolumeSpec = Field(description="data 卷规格")
    log: ServiceVolumeSpec = Field(description="log 卷规格")


class UserServiceStorageSpec(ApiSchema):
    """普通用户可见的单元存储规格。"""

    data: UserServiceVolumeSpec = Field(description="data 卷规格")
    log: UserServiceVolumeSpec = Field(description="log 卷规格")


class ServiceUnit(ApiSchema):
    """子服务下的单元信息。"""

    name: str = Field(description="单元名称")
    type: str = Field(description="单元类型")
    cpuArchitecture: str | None = Field(default=None, description="单元所在主机的机器可读 CPU 架构")
    cpuArchitectureDisplayName: str | None = Field(default=None, description="单元所在主机的用户可读 CPU 架构名称")
    version: str | None = Field(default=None, description="单元真实部署版本")
    runningStatus: str = Field(description="单元运行状态")
    hostName: str = Field(description="单元所在主机名称")
    hostIp: str = Field(description="单元所在主机 IP")
    ip: str = Field(description="单元服务 IPv4 地址")
    ipv6: str | None = Field(default=None, description="单元服务 IPv6 地址")
    cpu: float | None = Field(default=None, description="CPU 核数")
    memoryGB: float | None = Field(default=None, description="内存大小，单位 GB")
    storage: ServiceStorageSpec = Field(description="单元存储规格")


class UserServiceUnit(ApiSchema):
    """普通用户可见的子服务下单元信息。"""

    name: str = Field(description="单元名称")
    type: str = Field(description="单元类型")
    cpuArchitecture: str | None = Field(default=None, description="单元所在主机的机器可读 CPU 架构")
    cpuArchitectureDisplayName: str | None = Field(default=None, description="单元所在主机的用户可读 CPU 架构名称")
    version: str | None = Field(default=None, description="单元真实部署版本")
    runningStatus: str = Field(description="单元运行状态")
    ip: str = Field(description="单元服务 IPv4 地址")
    ipv6: str | None = Field(default=None, description="单元服务 IPv6 地址")
    cpu: float | None = Field(default=None, description="CPU 核数")
    memoryGB: float | None = Field(default=None, description="内存大小，单位 GB")
    storage: UserServiceStorageSpec = Field(description="单元存储规格")


class ChildService(ApiSchema):
    """服务组中的子服务信息。"""

    name: str = Field(description="子服务名称")
    type: str = Field(description="子服务类型")
    version: str | None = Field(default=None, description="子服务版本")
    port: int | None = Field(default=None, description="服务端口")
    runningStatus: str = Field(description="子服务运行状态")
    units: list[ServiceUnit] = Field(default_factory=list, description="子服务下的单元列表")


class UserChildService(ApiSchema):
    """普通用户可见的服务组子服务信息。"""

    name: str = Field(description="子服务名称")
    type: str = Field(description="子服务类型")
    version: str | None = Field(default=None, description="子服务版本")
    port: int | None = Field(default=None, description="服务端口")
    runningStatus: str = Field(description="子服务运行状态")
    units: list[UserServiceUnit] = Field(default_factory=list, description="子服务下的单元列表")


class UpdateStorageVolumeRequest(ApiSchema):
    """存储卷容量更新请求。"""

    sizeGB: float = Field(gt=0, description="更新后的卷容量，单位 GB")


class UpdateStorageSpecRequest(ApiSchema):
    """存储更新请求。"""

    data: UpdateStorageVolumeRequest | None = Field(default=None, description="更新后的 data 卷规格")
    log: UpdateStorageVolumeRequest | None = Field(default=None, description="更新后的 log 卷规格")

    @model_validator(mode="after")
    def validate_storage_fields(self) -> "UpdateStorageSpecRequest":
        """要求至少传入一个存储字段。"""

        if self.data is None and self.log is None:
            raise ValueError("at least one storage field must be provided")
        return self


class UpdateServiceResourceRequest(ApiSchema):
    """`PUT /services/{name}/resource` 的请求模型。"""

    childServiceType: str = Field(description="目标子服务类型，例如 mysql、proxy")
    platformAuto: bool | None = Field(default=None, description="是否由平台自动分配规格")
    cpu: float | None = Field(default=None, gt=0, description="更新后的 CPU 核数")
    memoryGB: float | None = Field(default=None, gt=0, description="更新后的内存大小，单位 GB")

    @model_validator(mode="after")
    def validate_resource_fields(self) -> "UpdateServiceResourceRequest":
        """要求至少传入一个资源字段。"""

        if self.platformAuto is None and self.cpu is None and self.memoryGB is None:
            raise ValueError("at least one of 'platformAuto', 'cpu' or 'memoryGB' must be provided")
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
    memory_gb: float = Field(description="内存大小，单位 GB")


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
    target_memory_gb: float | None = Field(default=None, gt=0, description="目标内存大小，单位 GB")


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

    data_volume_gb: float = Field(description="data 卷容量，单位 GB")
    log_volume_gb: float = Field(description="log 卷容量，单位 GB")


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
    target_data_volume_gb: float | None = Field(default=None, gt=0, description="目标 data 卷容量，单位 GB")
    target_log_volume_gb: float | None = Field(default=None, gt=0, description="目标 log 卷容量，单位 GB")


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
    unitNames: list[str] | None = Field(
        default=None,
        description="指定升级的单元名称列表；不传时表示升级该子服务下所有单元",
    )


class ImageUpgradeTarget(ApiSchema):
    """可升级镜像目标。"""

    image: str = Field(description="目标镜像，例如 mysql:8.0.37")
    version: str = Field(description="目标版本号，例如 8.0.37")


class ImageUpgradeCapabilityResponse(ApiSchema):
    """`GET /image-upgrade-capabilities` 的响应模型。"""

    supported: bool = Field(description="是否支持镜像升级")
    availableTargets: list[ImageUpgradeTarget] = Field(default_factory=list, description="可选镜像和版本候选")


class ServiceBackupRequest(ApiSchema):
    """`POST /services/{name}/backup` 的请求模型。"""

    scope: str = Field(description="备份范围：service 或 unit")
    backupType: str = Field(description="备份类型，例如 full")
    retentionDays: int = Field(gt=0, description="备份保留天数")
    unitName: str | None = Field(default=None, description="目标 unit 名称")
    options: dict[str, object] | None = Field(default=None, description="服务类别相关备份参数")
    remark: str | None = Field(default=None, description="备份备注")


class BackupCapabilityField(ApiSchema):
    """备份发起参数字段描述。"""

    name: str = Field(description="DBAAS 接口字段名")
    type: str = Field(description="字段类型")
    required: bool = Field(default=False, description="是否必填")
    enumValues: list[str] | None = Field(default=None, description="可选枚举值")
    min: int | None = Field(default=None, description="最小值")
    max: int | None = Field(default=None, description="最大值")
    description: str | None = Field(default=None, description="字段说明")
    requiresUserInput: bool = Field(default=False, description="是否需要用户补充")


class BackupRuntimeHints(ApiSchema):
    """备份发起运行提示，不作为 precheck 阻断。"""

    backupRunning: bool = Field(default=False, description="当前目标是否已有备份执行中")
    runningBackups: list[dict[str, object]] = Field(default_factory=list, description="正在执行的备份摘要")


class BackupCapabilityResponse(ApiSchema):
    """`GET /backup-task-capabilities` 的响应模型。"""

    supported: bool = Field(description="是否支持发起备份")
    serviceType: str | None = Field(default=None, description="服务类型")
    scopeValues: list[str] = Field(default_factory=list, description="支持的备份范围")
    fields: list[BackupCapabilityField] = Field(default_factory=list, description="参数字段")
    resolvedTarget: dict[str, object] | None = Field(default=None, description="按名称解析出的目标")
    runtimeHints: BackupRuntimeHints | None = Field(default=None, description="运行提示")


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
    ownerAccount: str | None = Field(default=None, description="服务负责人账号")
    ownerName: str | None = Field(default=None, description="服务负责人姓名")
    businessSystemName: str | None = Field(default=None, description="服务所属业务系统名称")
    businessSubsystemName: str | None = Field(default=None, description="服务所属业务子系统名称")
    subsystem: str | None = Field(default=None, description="兼容字段：服务组所属子系统")
    siteId: str = Field(description="服务组所属站点 ID")
    siteName: str = Field(description="服务组所属站点名称")
    areaName: str | None = Field(default=None, description="服务组所属区域名称")
    sharding: bool | None = Field(default=None, description="是否为分片结构")
    runningStatus: str = Field(description="服务组运行状态")
    replicationStatus: str | None = Field(default=None, description="复制或同步状态")
    childServices: list[ChildService] = Field(default_factory=list, description="服务组下的子服务列表")
    backupStrategy: BackupStrategySummary | None = Field(
        default=None,
        description="服务组备份策略摘要，运行时可由备份策略数据合并得到",
    )


class UserServiceDetailResponse(ApiSchema):
    """普通用户可见的 `GET /services/{name}` 响应模型。"""

    name: str = Field(description="服务组名称")
    type: str = Field(description="服务组类型")
    user: str | None = Field(default=None, description="服务组所属用户")
    businessSystemName: str | None = Field(default=None, description="服务所属业务系统名称")
    businessSubsystemName: str | None = Field(default=None, description="服务所属业务子系统名称")
    subsystem: str | None = Field(default=None, description="兼容字段：服务组所属子系统")
    siteName: str = Field(description="服务组所属站点名称")
    areaName: str | None = Field(default=None, description="服务组所属区域名称")
    sharding: bool | None = Field(default=None, description="是否为分片结构")
    runningStatus: str = Field(description="服务组运行状态")
    replicationStatus: str | None = Field(default=None, description="复制或同步状态")
    childServices: list[UserChildService] = Field(default_factory=list, description="服务组下的子服务列表")
    backupStrategy: BackupStrategySummary | None = Field(
        default=None,
        description="服务组备份策略摘要，运行时可由备份策略数据合并得到",
    )

"""服务查询和更新相关接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import CurrentUser, ensure_service_access, get_current_user, resolve_service_user_filter
from app.schemas import (
    BackupCapabilityResponse,
    CreateTaskResponse,
    ImageUpgradeCapabilityResponse,
    PrecheckServiceResourceUpdateRequest,
    PrecheckServiceResourceUpdateResponse,
    PrecheckServiceStorageUpdateRequest,
    PrecheckServiceStorageUpdateResponse,
    ServiceBackupRequest,
    ServiceDetailResponse,
    ServiceImageUpgradeRequest,
    UserServiceDetailResponse,
    UpdateServiceResourceRequest,
    UpdateServiceStorageRequest,
)

from app.store import (
    ChildServiceTypeNotFoundError,
    JsonDataStore,
    ServiceNotFoundError,
    ServiceUnitNotFoundError,
)

router = APIRouter(tags=["services"])


def get_store(request: Request) -> JsonDataStore:
    """从应用状态中获取内存数据存储。"""

    return request.app.state.store


@router.get("/services/{name}", response_model=ServiceDetailResponse | UserServiceDetailResponse)
def get_service(
    name: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> ServiceDetailResponse:
    """按服务组名称查询完整服务详情。"""

    store = get_store(request)
    service_detail = ensure_service_access(store, current_user, name)
    if current_user.is_admin:
        return ServiceDetailResponse.model_validate(store._public_service_detail(service_detail))
    return UserServiceDetailResponse.model_validate(store._public_service_detail_for_user(service_detail))


@router.get("/services", response_model=list[ServiceDetailResponse] | list[UserServiceDetailResponse])
def list_services(
    request: Request,
    user: str | None = Query(default=None, description="按服务组 user 精确过滤"),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ServiceDetailResponse]:
    """查询当前已加载到内存的服务组，可按 user 过滤。"""

    store = get_store(request)
    effective_user = resolve_service_user_filter(current_user, user)
    if current_user.is_admin:
        return [
            ServiceDetailResponse.model_validate(service_detail)
            for service_detail in store.list_service_details(user=effective_user)
        ]
    return [
        UserServiceDetailResponse.model_validate(store._public_service_detail_for_user(service_detail))
        for service_detail in store.list_service_seeds(user=effective_user)
    ]


@router.get("/backup-task-capabilities", response_model=BackupCapabilityResponse)
def describe_backup_task_capabilities(
    request: Request,
    serviceType: str | None = Query(default=None, description="服务类别"),
    serviceName: str | None = Query(default=None, description="服务名称"),
    unitName: str | None = Query(default=None, description="unit 名称"),
    current_user: CurrentUser = Depends(get_current_user),
) -> BackupCapabilityResponse:
    """返回手动备份发起能力和轻量运行提示。"""

    if not any([serviceType, serviceName, unitName]):
        raise HTTPException(status_code=400, detail="at least one target query parameter is required")
    store = get_store(request)
    try:
        result = store.describe_backup_task_capabilities(
            service_type=serviceType,
            service_name=serviceName,
            unit_name=unitName,
        )
    except ServiceNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"service '{error.args[0]}' not found") from None
    except ChildServiceTypeNotFoundError as error:
        raise HTTPException(status_code=502, detail=f"child service '{error.args[0]}' not found") from None
    except ServiceUnitNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"unit '{error.args[0]}' not found") from None

    resolved = result.get("resolvedTarget")
    if isinstance(resolved, dict):
        resolved_service_name = resolved.get("serviceName")
        if isinstance(resolved_service_name, str) and resolved_service_name:
            ensure_service_access(store, current_user, resolved_service_name)
    return BackupCapabilityResponse.model_validate(result)


@router.get("/image-upgrade-capabilities", response_model=ImageUpgradeCapabilityResponse)
def describe_image_upgrade_capabilities(
    request: Request,
    serviceName: str = Query(description="服务名称"),
    childServiceType: str = Query(description="目标子服务类型"),
    current_user: CurrentUser = Depends(get_current_user),
) -> ImageUpgradeCapabilityResponse:
    """返回指定服务/子服务的可升级镜像和版本候选。"""

    store = get_store(request)
    ensure_service_access(store, current_user, serviceName)
    try:
        result = store.describe_image_upgrade_capabilities(
            serviceName,
            child_service_type=childServiceType,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{serviceName}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{serviceName}' has no child service type '{childServiceType}'",
        ) from None
    return ImageUpgradeCapabilityResponse.model_validate(result)


@router.post("/api/v1/prechecks/service-resource-update", response_model=PrecheckServiceResourceUpdateResponse)
def precheck_service_resource_update(
    payload: PrecheckServiceResourceUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> PrecheckServiceResourceUpdateResponse:
    """返回服务 CPU/内存资源调整前的只读预检事实。"""

    store = get_store(request)
    ensure_service_access(store, current_user, payload.service_name)
    try:
        result = store.precheck_service_resource_update(
            payload.service_name,
            child_service_type=payload.child_service_type,
            target_cpu_cores=payload.target_cpu_cores,
            target_memory_gb=payload.target_memory_gb,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{payload.service_name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{payload.service_name}' has no child service type '{payload.child_service_type}'",
        ) from None
    return PrecheckServiceResourceUpdateResponse.model_validate(result)


@router.post("/api/v1/prechecks/service-storage-update", response_model=PrecheckServiceStorageUpdateResponse)
def precheck_service_storage_update(
    payload: PrecheckServiceStorageUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> PrecheckServiceStorageUpdateResponse:
    """返回服务 data/log 卷容量调整前的只读预检事实。"""

    store = get_store(request)
    ensure_service_access(store, current_user, payload.service_name)
    try:
        result = store.precheck_service_storage_update(
            payload.service_name,
            child_service_type=payload.child_service_type,
            target_data_volume_gb=payload.target_data_volume_gb,
            target_log_volume_gb=payload.target_log_volume_gb,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{payload.service_name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{payload.service_name}' has no child service type '{payload.child_service_type}'",
        ) from None
    return PrecheckServiceStorageUpdateResponse.model_validate(result)


@router.put("/services/{name}/resource", response_model=ServiceDetailResponse | UserServiceDetailResponse)
def update_service_resource(
    name: str,
    payload: UpdateServiceResourceRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> ServiceDetailResponse:
    """按子服务类型更新资源规格。"""

    store = get_store(request)
    ensure_service_access(store, current_user, name)
    try:
        service_detail = store.update_service_resources(
            name,
            child_service_type=payload.childServiceType,
            platform_auto=payload.platformAuto,
            cpu=payload.cpu,
            memory_gb=payload.memoryGB,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{name}' has no child service type '{payload.childServiceType}'",
        ) from None
    if current_user.is_admin:
        return ServiceDetailResponse.model_validate(service_detail)
    updated_seed = store.get_service_seed(name)
    if updated_seed is None:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found")
    return UserServiceDetailResponse.model_validate(store._public_service_detail_for_user(updated_seed))


@router.put("/services/{name}/storage", response_model=ServiceDetailResponse | UserServiceDetailResponse)
def update_service_storage(
    name: str,
    payload: UpdateServiceStorageRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> ServiceDetailResponse:
    """按子服务类型更新存储规格。"""

    store = get_store(request)
    ensure_service_access(store, current_user, name)
    storage = payload.storage
    try:
        service_detail = store.update_service_storage(
            name,
            child_service_type=payload.childServiceType,
            platform_auto=payload.platformAuto,
            data_volume_size_gb=storage.data.sizeGB if storage is not None and storage.data is not None else None,
            log_volume_size_gb=storage.log.sizeGB if storage is not None and storage.log is not None else None,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{name}' has no child service type '{payload.childServiceType}'",
        ) from None
    if current_user.is_admin:
        return ServiceDetailResponse.model_validate(service_detail)
    updated_seed = store.get_service_seed(name)
    if updated_seed is None:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found")
    return UserServiceDetailResponse.model_validate(store._public_service_detail_for_user(updated_seed))


@router.post("/services/{name}/backup", response_model=CreateTaskResponse)
def create_service_backup_task(
    name: str,
    payload: ServiceBackupRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> CreateTaskResponse:
    """创建手动备份异步任务，并返回统一 taskId。"""

    store = get_store(request)
    ensure_service_access(store, current_user, name)
    try:
        task = store.create_service_backup_task(
            name,
            scope=payload.scope,
            backup_type=payload.backupType,
            retention_days=payload.retentionDays,
            unit_name=payload.unitName,
            options=payload.options,
            remark=payload.remark,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found") from None
    except ChildServiceTypeNotFoundError as error:
        raise HTTPException(status_code=502, detail=f"service '{name}' has no child service '{error.args[0]}'") from None
    except ServiceUnitNotFoundError as error:
        raise HTTPException(status_code=400, detail=f"service '{name}' has no unit '{error.args[0]}'") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return CreateTaskResponse(taskId=task["taskId"])


@router.post("/services/{name}/image-upgrade", response_model=CreateTaskResponse)
def create_service_image_upgrade_task(
    name: str,
    payload: ServiceImageUpgradeRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> CreateTaskResponse:
    """创建镜像升级异步任务，并返回 taskId。"""

    store = get_store(request)
    ensure_service_access(store, current_user, name)
    try:
        task = store.create_service_image_upgrade_task(
            name,
            child_service_type=payload.childServiceType,
            image=payload.image,
            version=payload.version,
            unit_names=payload.unitNames,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{name}' has no child service type '{payload.childServiceType}'",
        ) from None
    except ServiceUnitNotFoundError as error:
        raise HTTPException(
            status_code=400,
            detail=f"service '{name}' has no unit names '{error.args[0]}' in child service type '{payload.childServiceType}'",
        ) from None
    return CreateTaskResponse(taskId=task["taskId"])

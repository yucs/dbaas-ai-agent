"""服务查询和更新相关接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import CurrentUser, ensure_service_access, get_current_user, resolve_service_owner_filter
from app.schemas import (
    CreateTaskResponse,
    ServiceDetailResponse,
    ServiceImageUpgradeRequest,
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


@router.get("/services/{name}", response_model=ServiceDetailResponse)
def get_service(
    name: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> ServiceDetailResponse:
    """按服务组名称查询完整服务详情。"""

    store = get_store(request)
    return ServiceDetailResponse.model_validate(ensure_service_access(store, current_user, name))


@router.get("/services", response_model=list[ServiceDetailResponse])
def list_services(
    request: Request,
    owner: str | None = Query(default=None, description="按服务组 owner 精确过滤"),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ServiceDetailResponse]:
    """查询当前已加载到内存的服务组，可按 owner 过滤。"""

    store = get_store(request)
    effective_owner = resolve_service_owner_filter(current_user, owner)
    return [
        ServiceDetailResponse.model_validate(service_detail)
        for service_detail in store.list_service_details(owner=effective_owner)
    ]


@router.put("/services/{name}/resource", response_model=ServiceDetailResponse)
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
            memory=payload.memory,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{name}' has no child service type '{payload.childServiceType}'",
        ) from None
    return ServiceDetailResponse.model_validate(service_detail)


@router.put("/services/{name}/storage", response_model=ServiceDetailResponse)
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
            data_volume_size=storage.dataVolumeSize if storage is not None else None,
            log_volume_size=storage.logVolumeSize if storage is not None else None,
        )
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{name}' not found") from None
    except ChildServiceTypeNotFoundError:
        raise HTTPException(
            status_code=502,
            detail=f"service '{name}' has no child service type '{payload.childServiceType}'",
        ) from None
    return ServiceDetailResponse.model_validate(service_detail)


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
            unit_ids=payload.unitIds,
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
            detail=f"service '{name}' has no unit ids '{error.args[0]}' in child service type '{payload.childServiceType}'",
        ) from None
    return CreateTaskResponse(taskId=task["taskId"])

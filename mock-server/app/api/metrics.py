"""监控数据查询接口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import CurrentUser, ensure_service_access, get_current_user
from app.schemas import HistoryMetricPoint, LatestMetricPoint
from app.store import (
    JsonDataStore,
    MetricCatalogError,
    MetricNotFoundError,
    ServiceNotFoundError,
    ServiceUnitNotFoundError,
)

router = APIRouter(tags=["metrics"])


def get_store(request: Request) -> JsonDataStore:
    """从应用状态中获取内存数据存储。"""

    return request.app.state.store


@router.get("/metrics/latest", response_model=list[LatestMetricPoint])
def list_latest_metrics(
    request: Request,
    metric_key: str = Query(description="监控项 key，必须存在于 dbaas_metric_catalog.json"),
    service_name: str | None = Query(default=None, description="服务组名称；普通用户必填"),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[LatestMetricPoint]:
    """按监控项查询最新监控数据。"""

    store = get_store(request)
    if service_name is None and not current_user.is_admin:
        raise HTTPException(status_code=422, detail="service_name is required for non-admin users")

    if service_name is not None:
        ensure_service_access(store, current_user, service_name)

    try:
        points = store.list_latest_metric_points(metric_key, service_name=service_name)
    except MetricNotFoundError:
        raise HTTPException(status_code=404, detail=f"metric_key '{metric_key}' not found") from None
    except ServiceNotFoundError:
        raise HTTPException(status_code=404, detail=f"service '{service_name}' not found") from None
    except MetricCatalogError as error:
        raise HTTPException(status_code=500, detail=str(error)) from None
    return [LatestMetricPoint.model_validate(point) for point in points]


@router.get("/units/{unit_name}/metrics/history", response_model=list[HistoryMetricPoint])
def list_unit_metric_history(
    unit_name: str,
    request: Request,
    metric_key: str = Query(description="监控项 key，必须存在于 dbaas_metric_catalog.json"),
    start_ts: int = Query(description="开始时间，Unix timestamp 秒数"),
    end_ts: int = Query(description="结束时间，Unix timestamp 秒数"),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[HistoryMetricPoint]:
    """按真实单元名称查询历史监控数据。"""

    now_ts = int(datetime.now(UTC).timestamp())
    if start_ts >= end_ts:
        raise HTTPException(status_code=422, detail="start_ts must be less than end_ts")
    if end_ts > now_ts:
        raise HTTPException(status_code=422, detail="end_ts must not be in the future")

    store = get_store(request)
    bindings = store.find_unit_bindings(unit_name)
    if not bindings:
        raise HTTPException(status_code=404, detail=f"unit '{unit_name}' not found")

    if not current_user.is_admin and not any(binding.get("user") == current_user.user for binding in bindings):
        raise HTTPException(
            status_code=403,
            detail=f"user '{current_user.user}' cannot access unit '{unit_name}'",
        )

    try:
        points = store.list_unit_metric_history(
            unit_name,
            metric_key,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    except MetricNotFoundError:
        raise HTTPException(status_code=404, detail=f"metric_key '{metric_key}' not found") from None
    except ServiceUnitNotFoundError:
        raise HTTPException(status_code=404, detail=f"unit '{unit_name}' not found") from None
    except MetricCatalogError as error:
        raise HTTPException(status_code=500, detail=str(error)) from None
    return [HistoryMetricPoint.model_validate(point) for point in points]

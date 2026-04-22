"""站点、集群、主机查询接口。"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import CurrentUser, require_admin_user
from app.schemas import (
    ClusterDetailResponse,
    ClusterSummary,
    HostDetailResponse,
    HostSummary,
    SiteDetailResponse,
    SiteSummary,
)
from app.store import ClusterNotFoundError, HostNotFoundError, JsonDataStore, SiteNotFoundError

router = APIRouter(tags=["platform"])


def get_store(request: Request) -> JsonDataStore:
    """从应用状态中获取内存数据存储。"""

    return request.app.state.store


@router.get("/sites", response_model=list[SiteSummary])
def list_sites(
    request: Request,
    _current_user: CurrentUser = Depends(require_admin_user),
) -> list[SiteSummary]:
    """查询全部站点摘要。"""

    store = get_store(request)
    return [SiteSummary.model_validate(site) for site in store.list_sites()]


@router.get("/sites/{site_id}", response_model=SiteDetailResponse)
def get_site(
    site_id: str,
    request: Request,
    _current_user: CurrentUser = Depends(require_admin_user),
) -> SiteDetailResponse:
    """按站点 ID 查询站点详情。"""

    store = get_store(request)
    try:
        site = store.get_site(site_id)
    except SiteNotFoundError:
        raise HTTPException(status_code=404, detail=f"site '{site_id}' not found") from None
    return SiteDetailResponse.model_validate(site)


@router.get("/clusters", response_model=list[ClusterSummary])
def list_clusters(
    request: Request,
    _current_user: CurrentUser = Depends(require_admin_user),
) -> list[ClusterSummary]:
    """查询全部集群摘要。"""

    store = get_store(request)
    return [ClusterSummary.model_validate(cluster) for cluster in store.list_clusters()]


@router.get("/clusters/{cluster_id}", response_model=ClusterDetailResponse)
def get_cluster(
    cluster_id: str,
    request: Request,
    _current_user: CurrentUser = Depends(require_admin_user),
) -> ClusterDetailResponse:
    """按集群 ID 查询集群详情。"""

    store = get_store(request)
    try:
        cluster = store.get_cluster(cluster_id)
    except ClusterNotFoundError:
        raise HTTPException(status_code=404, detail=f"cluster '{cluster_id}' not found") from None
    return ClusterDetailResponse.model_validate(cluster)


@router.get("/hosts", response_model=list[HostSummary])
def list_hosts(
    request: Request,
    _current_user: CurrentUser = Depends(require_admin_user),
) -> list[HostSummary]:
    """查询全部主机摘要。"""

    store = get_store(request)
    return [HostSummary.model_validate(host) for host in store.list_hosts()]


@router.get("/hosts/{host_id}", response_model=HostDetailResponse)
def get_host(
    host_id: str,
    request: Request,
    _current_user: CurrentUser = Depends(require_admin_user),
) -> HostDetailResponse:
    """按主机 ID 查询主机详情。"""

    store = get_store(request)
    try:
        host = store.get_host(host_id)
    except HostNotFoundError:
        raise HTTPException(status_code=404, detail=f"host '{host_id}' not found") from None
    return HostDetailResponse.model_validate(host)

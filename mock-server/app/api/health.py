"""健康检查接口。"""

from fastapi import APIRouter


router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """返回服务健康状态。"""

    return {"status": "ok"}

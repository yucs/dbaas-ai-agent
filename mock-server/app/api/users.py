"""用户查询接口。"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import CurrentUser, ensure_user_access, get_current_user
from app.schemas import UserDetailResponse, UserSummary
from app.store import JsonDataStore

router = APIRouter(tags=["users"])


def get_store(request: Request) -> JsonDataStore:
    """从应用状态中获取内存数据存储。"""

    return request.app.state.store


@router.get("/users", response_model=list[UserSummary])
def list_users(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[UserSummary]:
    """查询用户列表，用户名直接等于服务组 user。"""

    store = get_store(request)
    user = None if current_user.is_admin else current_user.user
    return [
        UserSummary.model_validate(user)
        for user in store.list_users(user=user)
    ]


@router.get("/users/{user}", response_model=UserDetailResponse)
def get_user(
    user: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> UserDetailResponse:
    """按用户名查询用户详情，用户名直接等于服务组 user。"""

    ensure_user_access(current_user, user)
    store = get_store(request)
    user_detail = store.get_user(user)
    if user_detail is None:
        raise HTTPException(status_code=404, detail=f"user '{user}' not found")
    return UserDetailResponse.model_validate(user_detail)

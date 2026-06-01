"""备份查询接口。"""

from fastapi import APIRouter, Depends, Request

from app.auth import CurrentUser, get_current_user
from app.schemas import BackupRecord
from app.store import JsonDataStore


router = APIRouter(tags=["backups"])


def get_store(request: Request) -> JsonDataStore:
    """从应用状态中获取内存数据存储。"""

    return request.app.state.store


@router.get("/backups", response_model=list[BackupRecord])
def list_backups(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[BackupRecord]:
    """查询当前身份可见且当前仍存在的备份记录。"""

    store = get_store(request)
    owner_user = None if current_user.is_admin else current_user.user
    return [BackupRecord.model_validate(item) for item in store.list_backups(owner_user=owner_user)]

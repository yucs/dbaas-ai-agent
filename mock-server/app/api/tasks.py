"""通用异步任务查询接口。"""

from fastapi import APIRouter, HTTPException, Request

from app.schemas import Task
from app.store import JsonDataStore, TaskNotFoundError

router = APIRouter(tags=["tasks"])


def get_store(request: Request) -> JsonDataStore:
    """从应用状态中获取内存数据存储。"""

    return request.app.state.store


@router.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str, request: Request) -> Task:
    """按任务 ID 查询通用异步任务状态。"""

    store = get_store(request)
    try:
        task = store.get_task(task_id)
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail=f"task '{task_id}' not found") from None
    return Task.model_validate(task)

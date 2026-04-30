"""DBaaS mock server 应用入口。"""

from pathlib import Path

from fastapi import FastAPI

from app.api import health_router, metrics_router, platform_router, services_router, tasks_router, users_router
from app.store import JsonDataStore


def create_app(task_unit_interval_seconds: float = 3.0) -> FastAPI:
    """创建并初始化 FastAPI 应用。"""

    app = FastAPI(title="DBaaS Mock Server")
    data_dir = Path(__file__).resolve().parents[1] / "data"
    app.state.store = JsonDataStore(
        data_dir=data_dir,
        task_unit_interval_seconds=task_unit_interval_seconds,
    )
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(platform_router)
    app.include_router(services_router)
    app.include_router(tasks_router)
    app.include_router(users_router)
    return app


app = create_app()

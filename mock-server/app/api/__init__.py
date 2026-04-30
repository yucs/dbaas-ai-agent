"""HTTP 路由定义。"""

from .health import router as health_router
from .metrics import router as metrics_router
from .platform import router as platform_router
from .services import router as services_router
from .tasks import router as tasks_router
from .users import router as users_router

__all__ = [
    "health_router",
    "metrics_router",
    "platform_router",
    "services_router",
    "tasks_router",
    "users_router",
]

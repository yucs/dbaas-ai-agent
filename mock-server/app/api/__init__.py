"""HTTP 路由定义。"""

from .health import router as health_router
from .platform import router as platform_router
from .services import router as services_router
from .tasks import router as tasks_router

__all__ = ["health_router", "platform_router", "services_router", "tasks_router"]

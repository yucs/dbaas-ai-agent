"""通用异步任务接口 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .service_detail import ApiSchema


class CreateTaskResponse(ApiSchema):
    """异步任务引用响应。"""

    taskId: str = Field(description="异步任务唯一标识")


class Task(ApiSchema):
    """通用异步任务详情结构。"""

    taskId: str = Field(description="异步任务唯一标识")
    type: str = Field(description="任务类型，例如 service.image.upgrade")
    status: str = Field(description="任务当前状态，例如 RUNNING、SUCCESS、FAILED")
    message: str | None = Field(default=None, description="任务当前状态说明")
    reason: str | None = Field(default=None, description="任务失败原因，成功或执行中时为空")
    resourceType: str = Field(description="任务操作的资源类型，例如 service")
    resourceName: str = Field(description="任务操作的资源名称，例如 mysql-xf2")
    result: dict[str, Any] | None = Field(default=None, description="任务成功后的业务结果")
    createdAt: str = Field(description="任务创建时间，UTC ISO8601 格式")
    updatedAt: str = Field(description="任务最近更新时间，UTC ISO8601 格式")

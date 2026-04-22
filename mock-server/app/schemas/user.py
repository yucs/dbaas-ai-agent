"""用户查询接口 schema。"""

from pydantic import Field

from .platform import ServiceGroupSummary
from .service_detail import ApiSchema


class UserSummary(ApiSchema):
    """用户摘要。"""

    user: str = Field(description="用户名，直接等于服务组 user")
    serviceGroupCount: int = Field(description="该用户拥有的服务组数量")
    environments: list[str] = Field(default_factory=list, description="该用户涉及的环境列表")
    subsystems: list[str] = Field(default_factory=list, description="该用户涉及的子系统列表")


class UserDetailResponse(UserSummary):
    """用户详情。"""

    serviceGroups: list[ServiceGroupSummary] = Field(
        default_factory=list,
        description="该用户拥有的服务组摘要列表",
    )

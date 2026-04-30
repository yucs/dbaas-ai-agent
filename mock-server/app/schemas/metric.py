"""监控接口 schema。"""

from typing import Any

from pydantic import Field

from .service_detail import ApiSchema


class LatestMetricPoint(ApiSchema):
    """最新监控点位。"""

    unit_name: str = Field(description="单元名称")
    service_type: str = Field(description="单元所属服务类型，例如 mysql、redis、proxy")
    value: Any = Field(description="监控值，具体类型由 dbaas_metric_catalog.json 的 value_type 决定")


class HistoryMetricPoint(ApiSchema):
    """历史监控点位。"""

    ts: int = Field(description="点位时间，Unix timestamp 秒数")
    value: Any = Field(description="监控值，具体类型由 dbaas_metric_catalog.json 的 value_type 决定")

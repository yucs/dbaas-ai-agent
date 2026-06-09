from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


MetricScope = Literal["admin", "user"]
MetricValueType = Literal["number", "string", "enum", "boolean"]


@dataclass(frozen=True, slots=True)
class MetricCatalogEntry:
    metric_key: str
    display_name: str
    service_type: str
    value_type: MetricValueType
    unit: str | None
    aliases: tuple[str, ...]
    description: str | None = None
    enum_values: tuple[str, ...] = ()
    normal_values: tuple[str, ...] = ()
    abnormal_values: tuple[str, ...] = ()

    def compact(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "metric_key": self.metric_key,
            "display_name": self.display_name,
            "service_type": self.service_type,
            "value_type": self.value_type,
            "unit": self.unit,
        }
        if self.description:
            payload["description"] = self.description
        if self.enum_values:
            payload["enum_values"] = list(self.enum_values)
        if self.normal_values:
            payload["normal_values"] = list(self.normal_values)
        if self.abnormal_values:
            payload["abnormal_values"] = list(self.abnormal_values)
        return payload


@dataclass(frozen=True, slots=True)
class MetricSnapshotPaths:
    data_path: Path
    meta_path: Path
    scope: MetricScope
    user: str | None
    key: str


@dataclass(frozen=True, slots=True)
class MetricSnapshotRef:
    metric_key: str
    scope: MetricScope
    user: str | None
    data_path: Path
    meta_path: Path
    meta: dict[str, Any]

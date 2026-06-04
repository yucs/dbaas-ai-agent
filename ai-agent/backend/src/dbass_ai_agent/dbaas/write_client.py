from __future__ import annotations

from typing import Any

import httpx

from dbass_ai_agent.identity.models import Identity

from .auth import DbaasAuthError, dbaas_identity_headers
from .config import DbaasConfig


class DbaasWriteClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "dbaas_request_failed",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


class DbaasWriteTimeout(DbaasWriteClientError):
    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(
            f"DBAAS 控制面在 {timeout_seconds} 秒内未返回结果。",
            error_type="dbaas_timeout",
        )
        self.timeout_seconds = timeout_seconds


class DbaasWriteClient:
    def __init__(self, config: DbaasConfig) -> None:
        self.config = config

    def get_service(
        self,
        identity: Identity,
        service_name: str,
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            identity,
            "GET",
            f"/services/{service_name}",
            timeout_seconds=timeout_seconds,
        )

    def update_service_resource(
        self,
        identity: Identity,
        service_name: str,
        *,
        child_service_type: str,
        platform_auto: bool | None = None,
        cpu: float | None = None,
        memory: float | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "childServiceType": child_service_type,
        }
        if platform_auto is not None:
            payload["platformAuto"] = platform_auto
        if cpu is not None:
            payload["cpu"] = cpu
        if memory is not None:
            payload["memory"] = memory
        return self._request_json(
            identity,
            "PUT",
            f"/services/{service_name}/resource",
            json=payload,
            timeout_seconds=timeout_seconds,
        )

    def precheck_service_resource_update(
        self,
        identity: Identity,
        service_name: str,
        *,
        child_service_type: str,
        target_cpu_cores: float | None = None,
        target_memory_gb: float | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "service_name": service_name,
            "child_service_type": child_service_type,
        }
        if target_cpu_cores is not None:
            payload["target_cpu_cores"] = target_cpu_cores
        if target_memory_gb is not None:
            payload["target_memory_gb"] = target_memory_gb
        result = self._request_json(
            identity,
            "POST",
            "/api/v1/prechecks/service-resource-update",
            json=payload,
            timeout_seconds=timeout_seconds,
        )
        _validate_precheck_response(
            result,
            required_fields=(
                "service_name",
                "child_service_type",
                "current_spec",
                "available_specs",
                "runtime",
                "metrics",
                "blocking_errors",
            ),
            object_fields=("current_spec", "runtime", "metrics"),
            list_fields=("available_specs",),
            endpoint_name="service-resource-update",
        )
        return result

    def precheck_service_storage_update(
        self,
        identity: Identity,
        service_name: str,
        *,
        child_service_type: str,
        target_data_volume_gb: float | None = None,
        target_log_volume_gb: float | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "service_name": service_name,
            "child_service_type": child_service_type,
        }
        if target_data_volume_gb is not None:
            payload["target_data_volume_gb"] = target_data_volume_gb
        if target_log_volume_gb is not None:
            payload["target_log_volume_gb"] = target_log_volume_gb
        result = self._request_json(
            identity,
            "POST",
            "/api/v1/prechecks/service-storage-update",
            json=payload,
            timeout_seconds=timeout_seconds,
        )
        _validate_precheck_response(
            result,
            required_fields=(
                "service_name",
                "child_service_type",
                "current_storage",
                "runtime",
                "metrics",
                "blocking_errors",
            ),
            object_fields=("current_storage", "runtime", "metrics"),
            list_fields=(),
            endpoint_name="service-storage-update",
        )
        return result

    def update_service_storage(
        self,
        identity: Identity,
        service_name: str,
        *,
        child_service_type: str,
        platform_auto: bool | None = None,
        data_volume_size: float | None = None,
        log_volume_size: float | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "childServiceType": child_service_type,
        }
        if platform_auto is not None:
            payload["platformAuto"] = platform_auto
        storage: dict[str, Any] = {}
        if data_volume_size is not None:
            storage["dataVolumeSize"] = data_volume_size
        if log_volume_size is not None:
            storage["logVolumeSize"] = log_volume_size
        if storage:
            payload["storage"] = storage
        return self._request_json(
            identity,
            "PUT",
            f"/services/{service_name}/storage",
            json=payload,
            timeout_seconds=timeout_seconds,
        )

    def create_service_image_upgrade_task(
        self,
        identity: Identity,
        service_name: str,
        *,
        child_service_type: str,
        image: str,
        version: str | None = None,
        unit_ids: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "childServiceType": child_service_type,
            "image": image,
        }
        if version is not None:
            payload["version"] = version
        if unit_ids is not None:
            payload["unitIds"] = unit_ids
        return self._request_json(
            identity,
            "POST",
            f"/services/{service_name}/image-upgrade",
            json=payload,
            timeout_seconds=timeout_seconds,
        )

    def describe_service_backup_capability(
        self,
        identity: Identity,
        *,
        service_type: str | None = None,
        service_name: str | None = None,
        unit_name: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if service_type is not None:
            params["serviceType"] = service_type
        if service_name is not None:
            params["serviceName"] = service_name
        if unit_name is not None:
            params["unitName"] = unit_name
        return self._request_json(
            identity,
            "GET",
            "/backup-task-capabilities",
            params=params,
            timeout_seconds=timeout_seconds,
        )

    def create_service_backup_task(
        self,
        identity: Identity,
        service_name: str,
        *,
        scope: str,
        backup_type: str,
        retention_days: int,
        unit_name: str | None = None,
        options: dict[str, Any] | None = None,
        remark: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope": scope,
            "backupType": backup_type,
            "retentionDays": retention_days,
        }
        if unit_name is not None:
            payload["unitName"] = unit_name
        if options is not None:
            payload["options"] = options
        if remark is not None:
            payload["remark"] = remark
        return self._request_json(
            identity,
            "POST",
            f"/services/{service_name}/backup",
            json=payload,
            timeout_seconds=timeout_seconds,
        )

    def get_task(
        self,
        identity: Identity,
        task_id: str,
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            identity,
            "GET",
            f"/tasks/{task_id}",
            timeout_seconds=timeout_seconds,
        )

    def _request_json(
        self,
        identity: Identity,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_seconds or self.config.request_timeout_seconds
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                response = client.request(
                    method,
                    f"{self.config.server_base_url}{path}",
                    headers=dbaas_identity_headers(identity),
                    json=json,
                    params=params,
                )
        except DbaasAuthError as exc:
            raise DbaasWriteClientError(
                f"当前用户身份无法生成 DBAAS 请求身份：{exc}",
                error_type="permission_identity_missing",
            ) from exc
        except httpx.TimeoutException as exc:
            raise DbaasWriteTimeout(timeout) from exc
        except httpx.HTTPError as exc:
            raise DbaasWriteClientError(
                f"DBAAS 控制面请求失败：{exc}",
                error_type="dbaas_request_failed",
            ) from exc

        if response.status_code >= 400:
            raise DbaasWriteClientError(
                _format_response_error(response),
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DbaasWriteClientError(
                "DBAAS 控制面返回了无法解析的 JSON。",
                error_type="dbaas_invalid_response",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise DbaasWriteClientError(
                "DBAAS 控制面返回结构不是对象。",
                error_type="dbaas_invalid_response",
                status_code=response.status_code,
            )
        return payload


def _validate_precheck_response(
    payload: dict[str, Any],
    *,
    required_fields: tuple[str, ...],
    object_fields: tuple[str, ...],
    list_fields: tuple[str, ...],
    endpoint_name: str,
) -> None:
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise DbaasWriteClientError(
            f"DBAAS precheck 响应缺少必需字段：{', '.join(missing)}。",
            error_type="dbaas_invalid_response",
        )

    for field in ("service_name", "child_service_type"):
        if not isinstance(payload[field], str):
            raise DbaasWriteClientError(
                f"DBAAS precheck 响应字段 `{field}` 必须是字符串。",
                error_type="dbaas_invalid_response",
            )

    for field in object_fields:
        if not isinstance(payload[field], dict):
            raise DbaasWriteClientError(
                f"DBAAS precheck 响应字段 `{field}` 必须是对象。",
                error_type="dbaas_invalid_response",
            )

    for field in list_fields:
        if not isinstance(payload[field], list):
            raise DbaasWriteClientError(
                f"DBAAS precheck 响应字段 `{field}` 必须是数组。",
                error_type="dbaas_invalid_response",
            )

    _validate_blocking_errors(payload["blocking_errors"], endpoint_name=endpoint_name)


def _validate_blocking_errors(value: Any, *, endpoint_name: str) -> None:
    if not isinstance(value, list):
        raise DbaasWriteClientError(
            f"DBAAS precheck 响应字段 `blocking_errors` 必须是数组：{endpoint_name}。",
            error_type="dbaas_invalid_response",
        )
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise DbaasWriteClientError(
                f"DBAAS precheck 响应字段 `blocking_errors[{index}]` 必须是对象。",
                error_type="dbaas_invalid_response",
            )
        if not isinstance(item.get("code"), str) or not isinstance(item.get("message"), str):
            raise DbaasWriteClientError(
                f"DBAAS precheck 响应字段 `blocking_errors[{index}]` 缺少字符串 code/message。",
                error_type="dbaas_invalid_response",
            )


def _format_response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        detail = response.text
    else:
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("message") or payload
        else:
            detail = payload
    if not detail:
        detail = response.reason_phrase
    return f"DBAAS 控制面返回错误 {response.status_code}：{detail}"

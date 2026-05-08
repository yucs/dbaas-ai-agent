from __future__ import annotations

from typing import Any

import httpx

from dbass_ai_agent.identity.models import Identity

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
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_seconds or self.config.request_timeout_seconds
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                response = client.request(
                    method,
                    f"{self.config.server_base_url}{path}",
                    headers=_identity_headers(identity),
                    json=json,
                )
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


def _identity_headers(identity: Identity) -> dict[str, str]:
    if identity.role == "admin":
        return {"Authorization": "Bearer admin"}
    if not identity.user:
        raise DbaasWriteClientError(
            "当前用户身份缺少 DBAAS 用户范围，无法执行 DBAAS 操作。",
            error_type="permission_identity_missing",
        )
    return {"Authorization": f"Bearer user:{identity.user}"}


def _format_response_error(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text
    if not detail:
        detail = response.reason_phrase
    return f"DBAAS 控制面返回错误 {response.status_code}：{detail}"

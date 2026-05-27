from __future__ import annotations

from dbass_ai_agent.identity.models import Identity


SYSTEM_ACTOR_USER = "dbaas-ai-agent"
SYSTEM_ACTOR_ROLE = "system"


class DbaasAuthError(RuntimeError):
    """Raised when DBAAS request headers cannot be built from identity."""


def dbaas_identity_headers(identity: Identity) -> dict[str, str]:
    actor_user = identity.user_id.strip()
    if not actor_user:
        raise DbaasAuthError("DBAAS actor user is required")
    if identity.role == "admin":
        authorization = "Bearer admin"
    elif identity.role == "user":
        authorization = "Bearer user"
    else:
        raise DbaasAuthError(f"unsupported DBAAS actor role: {identity.role}")
    return _headers(
        authorization=authorization,
        actor_user=actor_user,
        actor_role=identity.role,
    )


def dbaas_system_headers() -> dict[str, str]:
    return _headers(
        authorization="Bearer admin",
        actor_user=SYSTEM_ACTOR_USER,
        actor_role=SYSTEM_ACTOR_ROLE,
    )


def dbaas_user_headers(actor_user: str) -> dict[str, str]:
    normalized_actor_user = actor_user.strip()
    if not normalized_actor_user:
        raise DbaasAuthError("DBAAS user actor is required")
    return _headers(
        authorization="Bearer user",
        actor_user=normalized_actor_user,
        actor_role="user",
    )


def _headers(*, authorization: str, actor_user: str, actor_role: str) -> dict[str, str]:
    return {
        "Authorization": authorization,
        "X-DBAAS-Actor-User": actor_user,
        "X-DBAAS-Actor-Role": actor_role,
    }

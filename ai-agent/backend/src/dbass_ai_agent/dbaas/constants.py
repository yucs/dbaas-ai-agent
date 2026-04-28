from __future__ import annotations

from pathlib import Path


SERVICES_KIND = "services"
SUPPORTED_KINDS = {SERVICES_KIND}

SERVICES_ENDPOINT = "/services"

ADMIN_SCOPE = "admin"
USERS_SCOPE = "users"

DATA_FILE_NAMES = {
    SERVICES_KIND: "services.json",
}
META_FILE_NAMES = {
    SERVICES_KIND: "services.meta.json",
}
LOCK_FILE_NAMES = {
    SERVICES_KIND: "services.lock",
}

SCHEMA_FILES = {
    SERVICES_KIND: Path("backend/schemas/services.v1.schema.json"),
}
SCHEMA_VERSIONS = {
    SERVICES_KIND: "services.v1",
}

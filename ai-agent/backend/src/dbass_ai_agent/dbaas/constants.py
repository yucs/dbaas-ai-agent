from __future__ import annotations

from pathlib import Path


SERVICES_KIND = "services"
SUPPORTED_KINDS = {SERVICES_KIND}

SERVICES_ENDPOINT = "/services"

ADMIN_SCOPE = "admin"

DATA_FILE_NAMES = {
    SERVICES_KIND: "services.json",
}
META_FILE_NAMES = {
    SERVICES_KIND: "services.meta.json",
}

SCHEMA_FILES = {
    SERVICES_KIND: Path("backend/schemas/services.v1.schema.json"),
}
SCHEMA_VERSIONS = {
    SERVICES_KIND: "services.v1",
}

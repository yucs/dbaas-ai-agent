from __future__ import annotations

from pathlib import Path


SERVICES_KIND = "services"
BACKUPS_KIND = "backups"
SUPPORTED_KINDS = {SERVICES_KIND, BACKUPS_KIND}

SERVICES_ENDPOINT = "/services"
BACKUPS_ENDPOINT = "/backups"

ADMIN_SCOPE = "admin"
USER_SCOPE = "user"
CONFIG_ROOT = Path("config")
SCHEMA_ROOT = CONFIG_ROOT / "schemas"
METRIC_CATALOG_FILE = CONFIG_ROOT / "dbaas_metric_catalog.json"

DATA_FILE_NAMES = {
    SERVICES_KIND: "services.json",
    BACKUPS_KIND: "backups.json",
}
META_FILE_NAMES = {
    SERVICES_KIND: "services.meta.json",
    BACKUPS_KIND: "backups.meta.json",
}

SCHEMA_FILES = {
    (SERVICES_KIND, ADMIN_SCOPE): SCHEMA_ROOT / "services.admin.v1.schema.json",
    (SERVICES_KIND, USER_SCOPE): SCHEMA_ROOT / "services.user.v1.schema.json",
    (BACKUPS_KIND, ADMIN_SCOPE): SCHEMA_ROOT / "backups.v1.schema.json",
    (BACKUPS_KIND, USER_SCOPE): SCHEMA_ROOT / "backups.v1.schema.json",
}
SCHEMA_VERSIONS = {
    (SERVICES_KIND, ADMIN_SCOPE): "services.admin.v1",
    (SERVICES_KIND, USER_SCOPE): "services.user.v1",
    (BACKUPS_KIND, ADMIN_SCOPE): "backups.v1",
    (BACKUPS_KIND, USER_SCOPE): "backups.v1",
}

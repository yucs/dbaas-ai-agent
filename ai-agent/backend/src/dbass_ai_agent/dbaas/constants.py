from __future__ import annotations

from pathlib import Path


SERVICES_KIND = "services"
SUPPORTED_KINDS = {SERVICES_KIND}

SERVICES_ENDPOINT = "/services"

ADMIN_SCOPE = "admin"
CONFIG_ROOT = Path("config")
SCHEMA_ROOT = CONFIG_ROOT / "schemas"
METRIC_CATALOG_FILE = CONFIG_ROOT / "dbaas_metric_catalog.json"

DATA_FILE_NAMES = {
    SERVICES_KIND: "services.json",
}
META_FILE_NAMES = {
    SERVICES_KIND: "services.meta.json",
}

SCHEMA_FILES = {
    SERVICES_KIND: SCHEMA_ROOT / "services.v1.schema.json",
}
SCHEMA_VERSIONS = {
    SERVICES_KIND: "services.v1",
}

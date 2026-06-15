from __future__ import annotations

from pathlib import Path


SERVICES_KIND = "services"
BACKUPS_KIND = "backups"
HOSTS_KIND = "hosts"
CLUSTERS_KIND = "clusters"
SUPPORTED_SCHEMA_KINDS = {SERVICES_KIND, BACKUPS_KIND, HOSTS_KIND, CLUSTERS_KIND}

SERVICES_ENDPOINT = "/services"
BACKUPS_ENDPOINT = "/backups"
HOSTS_ENDPOINT = "/hosts"
CLUSTERS_ENDPOINT = "/clusters"

ADMIN_SCOPE = "admin"
USER_SCOPE = "user"
CONFIG_ROOT = Path("config")
SCHEMA_ROOT = CONFIG_ROOT / "schemas"
METRIC_CATALOG_FILE = CONFIG_ROOT / "dbaas_metric_catalog.json"

DATA_FILE_NAMES = {
    SERVICES_KIND: "services.json",
    BACKUPS_KIND: "backups.json",
    HOSTS_KIND: "hosts.json",
    CLUSTERS_KIND: "clusters.json",
}
META_FILE_NAMES = {
    SERVICES_KIND: "services.meta.json",
    BACKUPS_KIND: "backups.meta.json",
    HOSTS_KIND: "hosts.meta.json",
    CLUSTERS_KIND: "clusters.meta.json",
}

SCHEMA_FILES = {
    (SERVICES_KIND, ADMIN_SCOPE): SCHEMA_ROOT / "services.admin.v1.schema.json",
    (SERVICES_KIND, USER_SCOPE): SCHEMA_ROOT / "services.user.v1.schema.json",
    (BACKUPS_KIND, ADMIN_SCOPE): SCHEMA_ROOT / "backups.v1.schema.json",
    (BACKUPS_KIND, USER_SCOPE): SCHEMA_ROOT / "backups.v1.schema.json",
    (HOSTS_KIND, ADMIN_SCOPE): SCHEMA_ROOT / "hosts.v1.schema.json",
    (CLUSTERS_KIND, ADMIN_SCOPE): SCHEMA_ROOT / "clusters.v1.schema.json",
}
SCHEMA_VERSIONS = {
    (SERVICES_KIND, ADMIN_SCOPE): "services.admin.v1",
    (SERVICES_KIND, USER_SCOPE): "services.user.v1",
    (BACKUPS_KIND, ADMIN_SCOPE): "backups.v1",
    (BACKUPS_KIND, USER_SCOPE): "backups.v1",
    (HOSTS_KIND, ADMIN_SCOPE): "hosts.v1",
    (CLUSTERS_KIND, ADMIN_SCOPE): "clusters.v1",
}

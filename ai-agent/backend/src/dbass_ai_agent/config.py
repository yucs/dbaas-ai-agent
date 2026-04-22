from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    data_root: Path
    frontend_root: Path
    demo_mode: bool


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name="dbass-ai-agent",
        data_root=Path(os.getenv("DBASS_AGENT_DATA_ROOT", APP_ROOT / "data" / "users")),
        frontend_root=Path(os.getenv("DBASS_AGENT_FRONTEND_ROOT", APP_ROOT / "frontend")),
        demo_mode=os.getenv("DBASS_AGENT_MODE", "demo").lower() != "deepagent",
    )

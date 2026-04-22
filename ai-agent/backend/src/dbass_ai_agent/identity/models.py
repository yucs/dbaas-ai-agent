from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


UserRole = Literal["admin", "user"]


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    role: UserRole
    user: str | None

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SessionPaths:
    user_root: Path
    sessions_root: Path
    index_path: Path
    session_root: Path
    meta_path: Path
    messages_path: Path
    approvals_path: Path


def build_session_paths(data_root: Path, user_id: str, session_id: str) -> SessionPaths:
    user_root = data_root / user_id
    sessions_root = user_root / "sessions"
    session_root = sessions_root / session_id
    return SessionPaths(
        user_root=user_root,
        sessions_root=sessions_root,
        index_path=sessions_root / "index.json",
        session_root=session_root,
        meta_path=session_root / "meta.json",
        messages_path=session_root / "messages.json",
        approvals_path=session_root / "approvals.json",
    )


def build_user_sessions_root(data_root: Path, user_id: str) -> Path:
    return data_root / user_id / "sessions"

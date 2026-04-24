from __future__ import annotations

from fastapi import APIRouter, HTTPException, status


router = APIRouter(prefix="/api/v1", tags=["runs"])


@router.get("/sessions/{session_id}/runs/{run_id}/events")
def stream_run_events(session_id: str, run_id: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="独立 run 事件流暂未启用，请使用消息流接口 /messages/stream。",
    )

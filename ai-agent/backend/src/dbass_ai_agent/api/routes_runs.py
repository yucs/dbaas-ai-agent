from __future__ import annotations

from fastapi import APIRouter, HTTPException, status


router = APIRouter(prefix="/api/v1", tags=["runs"])


@router.get("/sessions/{session_id}/runs/{run_id}/events")
def stream_run_events(session_id: str, run_id: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="第一阶段暂未启用 SSE 流式事件接口。",
    )

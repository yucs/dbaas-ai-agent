from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from dbass_ai_agent.api.deps import close_agent_runtime, get_app_settings
from dbass_ai_agent.api.routes_chat import router as chat_router
from dbass_ai_agent.api.routes_runs import router as runs_router
from dbass_ai_agent.api.routes_sessions import router as sessions_router


def create_app() -> FastAPI:
    settings = get_app_settings()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(runs_router)

    def static_file_response(path: Path) -> FileResponse:
        return FileResponse(
            path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": "deepagent"}

    @app.get("/")
    def index() -> FileResponse:
        return static_file_response(settings.frontend_root / "index.html")

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return static_file_response(settings.frontend_root / "app.js")

    @app.get("/styles.css")
    def styles_css() -> FileResponse:
        return static_file_response(settings.frontend_root / "styles.css")

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await close_agent_runtime()

    return app


app = create_app()

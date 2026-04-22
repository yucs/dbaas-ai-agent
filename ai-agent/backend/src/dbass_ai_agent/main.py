from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from dbass_ai_agent.api.deps import get_app_settings
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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": "demo" if settings.demo_mode else "deepagent"}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(settings.frontend_root / "index.html")

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return FileResponse(settings.frontend_root / "app.js")

    @app.get("/styles.css")
    def styles_css() -> FileResponse:
        return FileResponse(settings.frontend_root / "styles.css")

    return app


app = create_app()

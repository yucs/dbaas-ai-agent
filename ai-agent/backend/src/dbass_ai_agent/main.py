from __future__ import annotations

import logging
from time import perf_counter
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from dbass_ai_agent.api.deps import close_agent_runtime, get_app_settings
from dbass_ai_agent.api.routes_chat import router as chat_router
from dbass_ai_agent.api.routes_runs import router as runs_router
from dbass_ai_agent.api.routes_sessions import router as sessions_router
from dbass_ai_agent.config import Settings
from dbass_ai_agent.infra.logging import (
    bind_log_context,
    elapsed_ms,
    extract_session_id_from_path,
    new_request_id,
    redact_log_text,
    reset_log_context,
    sanitize_log_value,
    setup_logging,
)


logger = logging.getLogger(__name__)
request_logger = logging.getLogger("dbass_ai_agent.request")


def _log_startup_settings(settings: Settings) -> None:
    logger.info(
        "application starting app=%s host=%s port=%s data_root=%s runtime_root=%s "
        "frontend_root=%s checkpoint_db=%s provider=%s model=%s base_url=%s "
        "compression_enabled=%s compression_trigger_tokens=%s compression_keep_messages=%s "
        "log_file=%s log_level=%s",
        settings.app_name,
        settings.host,
        settings.port,
        settings.data_root,
        settings.runtime_root,
        settings.frontend_root,
        settings.checkpoint_db,
        settings.provider_kind,
        settings.model or "-",
        settings.base_url or "-",
        settings.compression_enabled,
        settings.soft_trigger_tokens,
        settings.keep_recent_messages,
        settings.log_file,
        settings.log_level,
    )


def create_app() -> FastAPI:
    settings = get_app_settings()
    setup_logging(settings)
    _log_startup_settings(settings)

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

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = new_request_id()
        session_id = extract_session_id_from_path(request.url.path)
        user_id = sanitize_log_value(request.headers.get("X-User-Id"))
        role = sanitize_log_value(request.headers.get("X-User-Role", "user"))
        token = bind_log_context(
            request_id=request_id,
            user_id=user_id,
            role=role,
            session_id=session_id,
        )
        request.state.request_id = request_id
        request.state.session_id = session_id
        request.state.user_id = user_id
        request.state.role = role

        started_at = perf_counter()
        body_size = request.headers.get("content-length") or "-"
        request_logger.debug(
            "request started method=%s path=%s body_bytes=%s",
            request.method,
            request.url.path,
            body_size,
        )
        if settings.log_request_body:
            body = await request.body()
            body_text = body.decode("utf-8", errors="replace")
            request_logger.debug(
                "request body body_bytes=%s body=%s",
                len(body),
                redact_log_text(body_text),
            )

        try:
            response = await call_next(request)
            duration_ms = elapsed_ms(started_at)
            if response.status_code >= 500:
                log_method = request_logger.error
            elif response.status_code >= 400:
                log_method = request_logger.warning
            else:
                log_method = request_logger.info
            log_method(
                "request completed method=%s path=%s status=%s duration_ms=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            return response
        except Exception:
            request_logger.exception(
                "request failed method=%s path=%s duration_ms=%s",
                request.method,
                request.url.path,
                elapsed_ms(started_at),
            )
            raise
        finally:
            reset_log_context(token)

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
        logger.info("application shutting down")
        await close_agent_runtime()

    return app


app = create_app()

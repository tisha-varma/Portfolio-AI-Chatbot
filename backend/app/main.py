"""FastAPI application factory, health checks, and route registration."""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import DEFAULT_ALLOWED_ORIGINS, Settings, get_settings
from app.knowledge_loader import load_core, load_sections_index
from app.logging_setup import setup_logging
from app.routes_chat import router as chat_router
from app.security import INTERNAL_MESSAGE, SecurityMiddleware

logger = logging.getLogger(__name__)


def get_optional_settings() -> Settings | None:
    """Return settings when available, or None so /ready can report not-ready."""
    try:
        return get_settings()
    except RuntimeError:
        logger.warning("Configuration is incomplete; readiness will fail.")
        return None


def _cors_origins(settings: Settings | None) -> list[str]:
    """Return configured origins, never a wildcard."""
    if settings is not None:
        candidates = settings.allowed_origins_list
    else:
        raw = os.environ.get("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)
        candidates = [item.strip() for item in raw.split(",") if item.strip()]

    origins = [origin for origin in candidates if origin and origin != "*"]
    if len(origins) != len(candidates):
        logger.warning("Ignoring a wildcard CORS origin; explicit origins are required.")
    return origins


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app, optionally bound to explicit settings."""
    setup_logging()
    resolved_settings = settings if settings is not None else get_optional_settings()
    application = FastAPI(title="Portfolio AI Chatbot", version="0.2.0")

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(resolved_settings),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    # Added last so it is the outermost middleware and covers every response.
    application.add_middleware(SecurityMiddleware, settings=resolved_settings)

    application.include_router(chat_router)

    @application.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """Return a generic body with a request id; keep details in logs."""
        request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        logger.error(
            "Unhandled application error",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "request_id": request_id,
                "route": request.url.path,
                "status": 500,
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal",
                "message": INTERNAL_MESSAGE,
                "request_id": request_id,
            },
        )

    @application.get("/health")
    def health() -> dict[str, str]:
        """Return a simple liveness response that needs no configuration."""
        return {"status": "ok"}

    @application.get("/ready")
    def ready(
        resolved: Settings | None = Depends(get_optional_settings),
    ) -> JSONResponse:
        """Confirm configuration and knowledge load without calling Groq."""
        if resolved is None or not resolved.groq_api_keys:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "configuration"},
            )

        knowledge = resolved.knowledge_dir
        try:
            load_core(
                knowledge / "core.json",
                environment=resolved.environment,
            )
            load_sections_index(knowledge / "sections" / "index.json")
        except Exception:  # noqa: BLE001 - readiness must report, never crash
            logger.exception("Readiness knowledge check failed")
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "knowledge"},
            )

        return JSONResponse(status_code=200, content={"status": "ready"})

    if settings is not None:
        application.dependency_overrides[get_settings] = lambda: settings
        application.dependency_overrides[get_optional_settings] = lambda: settings

    return application


app = create_app()

"""Application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings
from app.core.database import init_db
from app.core.errors import register_error_handlers
from app.modules.privacy.middleware import AuditMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fine for the prototype; production uses Alembic migrations instead.
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Presence-aware OPD scheduling for government hospitals. "
            "Ten modules: presence detection, duty roster, multi-channel booking, "
            "AI scheduling, live queue, notifications, emergency priority, "
            "analytics, government integration, and security & privacy."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Room 10: record every state-changing request. Registered before the
    # routers so it wraps all of them.
    app.add_middleware(AuditMiddleware)

    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["Meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()

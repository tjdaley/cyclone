"""
app/main.py - FastAPI application factory.

Configures middleware, mounts routers, and performs startup validation.
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from middleware.auth_middleware import AuthMiddleware
from routers import admin, auth_flow, billing, clients, discovery, health, matters, staff
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.

    Code before ``yield`` runs at startup; code after runs at shutdown.
    """
    LOGGER.info(
        "Cyclone API started | env=%s llm_vendor=%s log_level=%s",
        "development" if settings.is_development else "production",
        settings.llm_vendor,
        settings.log_level,
    )
    yield
    # Shutdown logic (connection pool cleanup, etc.) goes here if needed.


def create_app() -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Startup checks:
    1. Pydantic validates all Settings fields on import — raises ValidationError
       before any requests are accepted if required fields are missing.
    2. SupabaseManager validates its credentials at instantiation (see
       dependencies.py — validated per request, not at startup to avoid
       blocking the process if DB is temporarily unreachable).

    :return: Configured FastAPI application.
    :rtype: FastAPI
    """
    app = FastAPI(
        title="Cyclone Legal Practice Management API",
        description="Backend API for Cyclone — billing, matters, discovery, and client portal.",
        version=settings.version,
        lifespan=_lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    # ── Middleware (applied in reverse order — last added = outermost) ──────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthMiddleware)

    # ── Routers ─────────────────────────────────────────────────────────────
    app.include_router(health.router)       # /api/health, /api/config (no auth)
    app.include_router(auth_flow.router)    # /api/v1/auth/me, /api/v1/auth/correlate-staff
    app.include_router(staff.router)        # /api/v1/staff
    app.include_router(clients.router)      # /api/v1/clients
    app.include_router(matters.router)      # /api/v1/matters
    app.include_router(billing.router)      # /api/v1/billing
    app.include_router(discovery.router)    # /api/v1/discovery
    app.include_router(admin.router)        # /api/v1/admin

    return app


def _cors_origins() -> list[str]:
    """
    Return allowed CORS origins based on environment.

    In development, localhost origins are permitted. In production, restrict
    to the configured host URL.

    :return: List of allowed origin strings.
    :rtype: list[str]
    """
    if settings.is_development:
        return [
            "http://localhost:3000",
            "http://localhost:5173",
            settings.host_url,
        ]
    return [settings.host_url]


app = create_app()

# -- Health check endpoint is unauthenticated, so we add it last to ensure it's not wrapped by
#    AuthMiddleware. --
@app.get("/api/healthcheck")
async def healthcheck():
    return {"status": "ok", "message": "Texas Law Brand Engine API is running."}


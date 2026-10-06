"""
app/main.py - FastAPI application factory.

Configures middleware, mounts routers, and performs startup validation.
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from middleware.auth_middleware import AuthMiddleware
from routers import admin, attorney_lead_responder, auth_flow, billing, category_rules, clients, costs, discovery, financial, fis, health, intake, kb, lead_agent_run, leads, matters, pleading, staff
from services.llm_service import llm_service
from util.loggerfactory import LoggerFactory
from util.config_fingerprint import log_startup_summary
from util.schema_check import check_schema
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.

    Code before ``yield`` runs at startup; code after runs at shutdown.
    """
    LOGGER.info(
        "Cyclone API started | env=%s log_level=%s llm_profiles: %s",
        "development" if settings.is_development else "production",
        settings.log_level,
        llm_service.describe_profiles(),
    )
    for problem in llm_service.validate_profiles():
        LOGGER.warning("LLM profile configuration: %s", problem)

    # A model field with no column is a 500 waiting for the right request, so
    # it belongs in the deploy log rather than in a stack trace during real
    # work. ERROR, not warning: these requests cannot succeed.
    # The fingerprint in the log beside the deploy that produced it, so
    # "which configuration was this node running on Tuesday" has an answer that
    # does not depend on the file still being there. Also the one place the
    # legacy-Supabase-key deadline and any dead settings get said out loud.
    log_startup_summary(settings)

    for problem in check_schema():
        LOGGER.error("Database schema: %s", problem)
    yield
    # Shutdown logic (connection pool cleanup, etc.) goes here if needed.


#: Postgres SQLSTATEs that mean "the value you sent is not allowed", not "the
#: server broke". Each becomes a 4xx with a sentence somebody can act on.
_DB_REJECTIONS = {
    # check_violation — a CHECK constraint refused the value. The overwhelmingly
    # common cause in this project is a migration that has not been run: the
    # code gained an enum member and the database's CHECK still lists the old
    # set. `schema_check` cannot see this (it compares COLUMNS only, by its own
    # documentation), so nothing reports it until somebody uses the new value.
    "23514": (422, "check"),
    # unique_violation
    "23505": (409, "unique"),
    # exclusion_violation -- two prices for one meter covering the same instant
    # (service_rates), or set_service_rate refusing a date before a later price.
    # The message is the database's own sentence and names no client data.
    "23P01": (409, "exclusion"),
    # foreign_key_violation
    "23503": (409, "foreign key"),
    # not_null_violation
    "23502": (422, "missing required value"),
}


async def _db_rejection_handler(request: Request, exc: APIError) -> JSONResponse:
    """
    Turn a constraint violation into an answer instead of a 500 and a traceback.

    **THE PRIMARY REASON IS PII, NOT TIDINESS.** PostgREST returns the offending
    row in ``details``:

        Failing row contains (1, 1, RiverSource Life Insurance Company,
        life_insurance, 0004, 0909 0823 2663 0 004, GABRIEL M SALMONS, ...)

    A client name and a full account number. Unhandled, FastAPI prints that whole
    traceback to the application log, which §6 forbids outright — and it does it
    for a constraint violation on ANY table, so the worst case is a row of a
    financial register. The row is never logged here and never returned.

    The SECOND reason is that a check violation is not a server fault. The value
    was refused, which is a 422, and the only fact worth passing on is the
    CONSTRAINT NAME: it names the table and column, it is not client data, and it
    is what turns "Internal Server Error" into "run migration 038".

    Anything not in `_DB_REJECTIONS` is re-raised untouched — a genuine database
    failure must keep looking like one.
    """
    code = str((exc.code or "")).strip()
    if code not in _DB_REJECTIONS:
        raise exc

    status, kind = _DB_REJECTIONS[code]
    # The message names the constraint; `details` holds the row. Take the first,
    # never the second.
    constraint = (exc.message or "").strip() or "a database constraint"
    LOGGER.error(
        "Database rejected a write: %s constraint, sqlstate=%s, path=%s. "
        "The offending row is deliberately not logged (it is client data).",
        kind, code, request.url.path,
    )

    detail = constraint
    if code == "23514":
        detail += (
            " — the database refused this value. If the application was just "
            "updated to offer a new option here, the matching migration has not "
            "been run on this database yet; migrations are applied by hand, in "
            "numeric order."
        )
    return JSONResponse(status_code=status, content={"detail": detail})


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

    # A constraint violation is the caller's problem, not a server fault, and
    # its `details` carries the offending row. See _db_rejection_handler.
    app.add_exception_handler(APIError, _db_rejection_handler)

    # ── Routers ─────────────────────────────────────────────────────────────
    app.include_router(health.router)       # /api/health, /api/config (no auth)
    app.include_router(auth_flow.router)    # /api/v1/auth/me, /api/v1/auth/correlate-staff
    app.include_router(staff.router)        # /api/v1/staff
    app.include_router(clients.router)      # /api/v1/clients
    app.include_router(intake.router)       # /api/v1/matters/intake (before matters — more specific path)
    app.include_router(matters.router)      # /api/v1/matters
    app.include_router(billing.router)      # /api/v1/billing
    app.include_router(discovery.router)    # /api/v1/discovery
    app.include_router(financial.router)   # /api/v1/matters/{id}/statements, financial-accounts
    app.include_router(fis.router)          # /api/v1/matters/{id}/fis, /api/v1/fis-settings
    app.include_router(category_rules.router)  # /api/v1/category-rules
    app.include_router(pleading.router)     # /api/v1/pleadings, /api/v1/claims, /api/v1/opposing-counsel
    app.include_router(leads.router)        # /api/v1/leads
    app.include_router(lead_agent_run.router)  # /api/v1/lead-agent-runs
    app.include_router(attorney_lead_responder.router)  # /api/v1/attorney-lead-responders
    app.include_router(kb.router)           # /api/v1/kb-articles
    app.include_router(admin.router)        # /api/v1/admin
    app.include_router(costs.router)        # /api/v1/costs — prices and spend (admin)

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


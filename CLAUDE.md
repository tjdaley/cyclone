# CLAUDE.md — Cyclone Project Conventions
> This file is read by Claude Code at the start of every session.
> Follow every rule here without exception unless the developer explicitly overrides one in the current session.

---

## 1. Project Overview

**Cyclone** is a legal practice management platform with a React frontend and FastAPI backend, persisted to Supabase (PostgreSQL). The full spec lives in `CYCLONE_PRD.md` at the repo root. Read it before beginning any non-trivial feature work.

**Repo:** https://github.com/tjdaley/cyclone.git  
**Stack:** React 18 (Vite) · FastAPI · Supabase (PostgreSQL) · Docker  
**Python:** 3.11+  
**Node:** 20+  
**Status:** Scaffolding complete — backend API, frontend SPA, and database DDL fully implemented. See §16 for what's not yet built.

---

## 2. Repository Layout

```
cyclone/
├── app/                           # FastAPI backend
│   ├── main.py                    # App factory, middleware, router registration
│   ├── dependencies.py            # get_db_manager, get_current_user, require_role
│   ├── Dockerfile                 # Python 3.11-slim
│   ├── requirements.txt           # pip dependencies
│   ├── util/
│   │   ├── settings.py            # Settings(BaseSettings) singleton — see §5
│   │   └── loggerfactory.py       # LoggerFactory — see §6
│   ├── middleware/
│   │   └── auth_middleware.py     # JWT validation, injects uid/role/email into request.state
│   ├── db/
│   │   ├── supabasemanager.py     # DatabaseManager ABC + SupabaseManager — see §7
│   │   ├── models/                # Pydantic domain + InDB models — see §8
│   │   │   ├── staff.py           # StaffMember, StaffRole, FullName, BarAdmission
│   │   │   ├── client.py          # Client, ClientStatus
│   │   │   ├── matter.py          # Matter, MatterStaff, BillingSplit, OpposingParty, MatterRateOverride
│   │   │   ├── billing_entry.py   # BillingEntry, EntryType
│   │   │   ├── billing_cycle.py   # BillingCycle, BillingCycleStatus
│   │   │   ├── trust_ledger.py    # TrustLedgerEntry (immutable — no updated_at)
│   │   │   ├── fee_agreement.py   # FeeAgreement, FeeAgreementStatus
│   │   │   ├── matter_event.py    # MatterEvent, EventType
│   │   │   ├── discovery.py       # DiscoveryRequest, DiscoveryResponse, RFASelection
│   │   │   ├── user_role.py       # UserRole, UserRoleType
│   │   │   └── audit_log.py       # AuditLog (immutable — id is UUID str, no updated_at)
│   │   └── repositories/          # BaseRepository[T] subclasses — see §8
│   │       ├── base_repo.py       # Generic CRUD wrapper
│   │       ├── staff.py           # Domain queries (by uid, slug, office)
│   │       ├── client.py          # Domain queries (by email, status)
│   │       ├── matter.py          # + MatterRateOverrideRepository, MatterStaffRepository,
│   │       │                      #   BillingSplitRepository, OpposingPartyRepository
│   │       ├── billing_entry.py   # Domain queries (by matter, unbilled, cycle, staff)
│   │       ├── billing_cycle.py   # Domain queries (by matter, open/closed)
│   │       ├── trust_ledger.py    # Domain queries (by matter, deposits) — append-only
│   │       ├── fee_agreement.py   # Domain queries (by matter, executed, pending)
│   │       ├── matter_event.py    # Domain queries (by matter, staff)
│   │       ├── discovery.py       # DiscoveryRequestRepository, DiscoveryResponseRepository
│   │       ├── user_role.py       # Auth entry point: lookup by supabase_uid, auth_email
│   │       └── audit_log.py       # Read-only queries (by entity, uid, action)
│   ├── routers/                   # FastAPI route handlers (thin — delegate to services)
│   │   ├── health.py              # GET /api/health, GET /api/config (public)
│   │   ├── auth_flow.py           # GET /api/v1/auth/me, POST /api/v1/auth/correlate-staff
│   │   ├── staff.py               # CRUD /api/v1/staff
│   │   ├── clients.py             # CRUD /api/v1/clients + conflict-check
│   │   ├── matters.py             # CRUD /api/v1/matters + rate overrides, staff, opposing parties
│   │   ├── billing.py             # Entries, cycles, balance, NL parse
│   │   ├── discovery.py           # Requests, ingest, responses
│   │   └── admin.py               # User roles, audit log (admin-only)
│   ├── services/                  # Business logic, LLM calls
│   │   ├── llm_service.py         # LLMService singleton — 5 vendors, complete() + complete_fast()
│   │   ├── billing_service.py     # Rate resolution, pro bono, NL parse, balance, cycle close
│   │   ├── audit_logger.py        # AuditLogger.log() — never re-raises on failure
│   │   └── conflict_service.py    # Phase 1 substring match; Phase 2 pg_trgm ready
│   └── schemas/                   # Pydantic request/response schemas
│       ├── common.py              # MessageResponse, DeletedResponse, PaginatedMeta
│       ├── staff.py               # StaffCreateRequest, StaffUpdateRequest, StaffResponse
│       ├── client.py              # + ConflictCheckRequest/Response
│       ├── matter.py              # + MatterRateOverrideRequest/Response
│       ├── billing.py             # + NLBillingParseRequest/Response, ClientBalanceResponse
│       └── discovery.py           # + DiscoveryIngestRequest
├── frontend/                      # React + Vite + TypeScript + Tailwind CSS
│   ├── Dockerfile                 # Multi-stage: node build → nginx
│   ├── nginx.conf                 # SPA fallback + /api proxy
│   ├── package.json               # react@18, react-router-dom@6, @supabase/supabase-js@2
│   ├── vite.config.ts             # Port 3000, /api proxy to localhost:8000
│   ├── tailwind.config.js         # Custom colors: navy, gold, off-white, etc.
│   ├── tsconfig.json              # Strict mode, ES2020
│   ├── index.html                 # Google Fonts (Inter, Playfair Display, JetBrains Mono)
│   ├── public/
│   │   └── favicon.svg            # Navy/gold brand mark
│   └── src/
│       ├── main.tsx               # React 18 entry point
│       ├── App.tsx                # BrowserRouter, public + protected routes
│       ├── index.css              # Tailwind directives + component layer (btn-primary, card, input)
│       ├── context/
│       │   └── AuthContext.tsx     # Session, profile, density, refreshProfile, signOut
│       ├── lib/
│       │   ├── supabaseClient.ts  # Auth only — never use for data queries
│       │   └── api.ts             # apiFetch<T>() with Bearer token injection
│       ├── components/
│       │   ├── ProtectedRoute.tsx # Guards /app/* routes — redirects unauthenticated/uncorrelated users
│       │   └── AppShell.tsx       # Sidebar nav, mobile hamburger, role-based menu items
│       └── pages/
│           ├── LandingPage.tsx    # Marketing page — hero, features, CTA
│           ├── LoginPage.tsx      # Google OAuth via Supabase
│           ├── AuthCallbackPage.tsx # Session exchange, routing to dashboard/onboarding
│           ├── OnboardingPage.tsx  # Staff correlation on first login
│           ├── AccessDeniedPage.tsx
│           └── app/
│               ├── DashboardPage.tsx  # Stats + recent matters table
│               ├── BillingPage.tsx    # Matter selector, NL parse → preview → commit, entries
│               ├── MattersPage.tsx    # Filterable/searchable matters list
│               ├── ClientsPage.tsx    # Conflict check panel + client list
│               ├── DiscoveryPage.tsx  # Discovery requests by matter
│               └── AdminPage.tsx      # Staff management, linked/unlinked accounts
├── db/
│   └── migrations/                # SQL DDL
│       ├── 001_extensions.sql     # pg_trgm, pgcrypto
│       ├── 002_tables.sql         # 17 tables
│       ├── 003_indexes_triggers.sql # Indexes, triggers (immutability, pro bono, splits)
│       ├── 004_functions.sql      # search_conflicts, resolve_billing_rate, views
│       ├── 005_rls.sql            # Row-Level Security policies
│       ├── 006_staff_auth_fields.sql # Nullable supabase_uid, auth_email for correlation
│       └── run_all.sql
├── docker-compose.yml             # Production: api (8000), frontend (3000/nginx)
├── docker-compose.override.yml    # Dev: hot reload, DEBUG logging
├── .env.example                   # All keys, no values — committed
├── .env.frontend.example          # Frontend-only env template
├── CYCLONE_PRD.md                 # Full product spec with implementation status
└── CLAUDE.md                      # This file
```

---

## 3. Golden Rules

These override everything else, including your own judgment about "better" patterns.

1. **Read before you write.** Before editing any existing module, read it fully. Never assume file contents match what you expect.
2. **No new patterns without justification.** If the codebase already has a pattern for something (logging, DB access, settings), use it. Do not introduce a second way to do the same thing.
3. **No raw SQL from the frontend.** All data mutations and queries go through FastAPI endpoints.
4. **No new `Settings()` instantiations.** Import `settings` from `util.settings` everywhere except `supabasemanager.py`, which has its own local `SETTINGS = Settings()` for historical reasons.
5. **No direct `logging.getLogger()` calls.** Use `LoggerFactory.create_logger(__name__)` exclusively.
6. **No LLM calls outside `app/services/llm_service.py`.** All AI completions are dispatched through the `LLMService` singleton.
7. **No `supabase.create_client()` outside `supabasemanager.py`.** All DB access goes through repository classes.
8. **Test before declaring done.** Run the relevant test suite or perform a manual smoke test against the dev Docker stack before marking a task complete.

---

## 4. Import Paths — CRITICAL

The backend runs from inside the `app/` directory:

```bash
uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000 --reload
```

**All Python imports use relative paths (no `app.` prefix):**

```python
# ✅ Correct — these are the actual import patterns in the codebase
from util.settings import settings
from util.loggerfactory import LoggerFactory
from db.supabasemanager import SupabaseManager, DatabaseManager
from db.models.staff import StaffMember, StaffMemberInDB
from db.repositories.staff import StaffRepository
from middleware.auth_middleware import AuthMiddleware
from services.billing_service import BillingService
from schemas.common import MessageResponse

# ❌ Wrong — do NOT use absolute imports with app. prefix
from app.util.settings import settings
```

---

## 5. Settings (`app/util/settings.py`)

- `Settings(BaseSettings)` is a Pydantic settings class loaded from `.env`
- The module-level singleton `settings = Settings()` is the **only** instance used throughout the app (except `supabasemanager.py`)
- **Import pattern:**
  ```python
  from util.settings import settings
  ```
- **Dynamic access:** Use `settings.getattr(item, default)` — do not use `getattr(settings, item)` directly
- **Adding new fields:** Add to the `Settings` class with a safe default (usually `""` or `None`); add the key to `.env.example` in the same PR
- **Never hardcode** API keys, URLs, or environment-specific values anywhere in application code

### Fields Claude Code Must Know About

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `version` | `str` | `"2026.04.05"` | API version string |
| `host_url` | `str` | `"http://localhost:8000"` | CORS and URL generation |
| `is_development` | `bool` | `False` | Gates debug behavior and docs endpoints |
| `firm_name` | `str` | `"Your Law Firm - Override in .env"` | Displayed in config endpoint |
| `supabase_url` | `str` | `""` | Supabase project URL |
| `supabase_service_role_key` | `str` | `""` | Used by backend only — never expose to frontend |
| `supabase_jwt_secret` | `str` | `""` | **Currently unused** — auth middleware uses JWKS/ES256 instead |
| `supabase_anon_key` | `str` | `""` | Used by frontend Supabase JS client |
| `llm_vendor` | `str` | `"gemini"` | Active LLM vendor: `"anthropic"`, `"gemini"`, `"openai"`, `"groq"`, `"deepseek"` |
| `llm_fast_vendor` | `str` | `"gemini"` | Vendor for latency-sensitive calls |
| `llm_temperature` | `float` | `0.1` | LLM temperature |
| `llm_top_p` | `float` | `0.1` | LLM top-p sampling |
| `stripe_secret_key` | `str` | `""` | Stripe server-side key |
| `stripe_publishable_key` | `str` | `""` | Safe to expose via `GET /api/config` |
| `time_increment_options` | `list` | `[0.1, 0.25, 0.5, 1.0]` | Valid billing time increments |
| `default_refresh_trigger_pct` | `float` | `0.40` | Default retainer refresh threshold |
| `log_level` | `str` | `"WARNING"` | Python logging level |
| `log_format` | `str` | format string | Python logging format |

---

## 6. Logging (`app/util/loggerfactory.py`)

### Usage — Required Pattern

Every module that needs logging must declare a module-level logger:

```python
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)
```

**Do not** call `logging.getLogger()` directly anywhere in application code.

### Rules

- Use `%s` format args in all log calls — never f-strings in log messages:
  ```python
  # Correct
  LOGGER.info("Billing entry committed: entry_id=%s matter_id=%s", entry_id, matter_id)
  
  # Wrong
  LOGGER.info(f"Billing entry committed: entry_id={entry_id}")
  ```
- **No PII in log messages.** Never log client names, financial amounts, SSNs, case facts, or attorney-client communications. Reference records by database ID only.
- Log level defaults to `settings.log_level` (`"WARNING"`) unless overridden at logger creation
- `LoggerFactory` sets `propagate = False` — do not set it again

### Log Levels

| Level | Use For |
|-------|---------|
| `DEBUG` | LLM prompt/response text, raw DB query params — dev only |
| `INFO` | Request received, record committed, bill generated, user authenticated |
| `WARNING` | LLM parse failure, Stripe webhook mismatch, conflict check error |
| `ERROR` | DB operation failed, unhandled exception, LLM call failure |
| `CRITICAL` | System cannot start, missing required config |

### Third-Party Log Levels

`supabasemanager.py` pins `httpx` and `postgrest` to `DEBUG` at module load. Do not override or re-set these in any other file.

---

## 7. Database Access (`app/db/supabasemanager.py`)

### Architecture

```
SupabaseManager(DatabaseManager)   ← concrete implementation
        ↑ used by
BaseRepository[T]                  ← generic CRUD base
        ↑ inherited by
BillingEntryRepository, StaffRepository, ...  ← domain repos (14 total)
        ↑ injected into
Service classes                    ← business logic
        ↑ called by
Route handlers (via Depends())
```

### SupabaseManager Rules

- `SupabaseManager` uses `supabase_service_role_key` — it **bypasses Supabase RLS**. Access control is enforced at the FastAPI route layer via `require_role()`.
- All methods retry 3 times on `APIError` with exponential backoff (2–10s).
- `select_one` returns `None` on PGRST116 (no row found) — callers must handle `None`.
- `insert()` will raise `ValueError` if `data` is a string. Always pass a `dict`:
  ```python
  # Correct
  repo.insert(my_model.model_dump())
  
  # Wrong — raises ValueError
  repo.insert(my_model.model_dump_json())
  ```
- `update()` always matches on the `id` field. There is no field-based update in the base class.
- `exists()` is a count-only check (selects `id` only) — use it for conflict checks and duplicate guards; do not use `select_one` for that purpose.

### Adding a New Repository

```python
# app/db/repositories/my_entity.py
from db.repositories.base_repo import BaseRepository
from db.models.my_entity import MyEntityInDB
from db.supabasemanager import DatabaseManager

class MyEntityRepository(BaseRepository[MyEntityInDB]):
    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "my_entities", MyEntityInDB)

    # Domain-specific methods beyond CRUD go here
    def get_by_matter(self, matter_id: int) -> list[MyEntityInDB]:
        return self.select_many(condition={"matter_id": matter_id})[0]
```

### Dependency Injection

Repositories are constructed in FastAPI route handlers via `Depends()`:

```python
# In a route handler
from dependencies import get_db_manager
from db.repositories.billing_entry import BillingEntryRepository

@router.get("/billing/{matter_id}")
def get_entries(matter_id: int, manager = Depends(get_db_manager)):
    repo = BillingEntryRepository(manager)
    return repo.get_by_matter(matter_id)
```

---

## 8. Model Conventions (`app/db/models/`)

Follow the established pattern:

```python
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

class MyEntity(BaseModel):
    """Domain model — business fields only, no DB metadata."""
    name: str = Field(..., description="Human-readable name")
    some_id: int = Field(..., description="FK to some other table")
    optional_field: Optional[str] = Field(default=None, description="...")

class MyEntityInDB(MyEntity):
    """Database model — extends domain model with DB-managed fields."""
    id: int = Field(..., description="Primary key, set by database")
    created_at: datetime = Field(..., description="Set by database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by database on update")
    model_config = ConfigDict(from_attributes=True)
```

### Rules
- Every field must have `description=` in `Field()`
- `InDB` models always add `id`, `created_at`, `updated_at` — nothing else
- **Exception:** `TrustLedgerEntry` and `AuditLog` are immutable — their `InDB` models have no `updated_at`
- **Exception:** `AuditLog` has `id: str` (UUID) rather than `int`
- `model_config = ConfigDict(from_attributes=True)` goes on `InDB` only
- Do not use `orm_mode = True` (deprecated Pydantic v1 syntax)

### Key Enums

| Model File | Enum | Values |
|-----------|------|--------|
| `staff.py` | `StaffRole` | attorney, paralegal, admin |
| `client.py` | `ClientStatus` | prospect, pending_conflict_check, conflict_flagged, active, inactive |
| `matter.py` | `MatterType` | divorce, child_custody, modification, enforcement, cps, probate, estate_planning, civil, other |
| `matter.py` | `MatterStatus` | intake, conflict_review, active, closed, archived |
| `billing_entry.py` | `EntryType` | time, expense, flat_fee |
| `billing_cycle.py` | `BillingCycleStatus` | open, closed |
| `trust_ledger.py` | `TrustTransactionType` | deposit, withdrawal, refund |
| `fee_agreement.py` | `FeeAgreementStatus` | draft, sent_to_client, executed, voided |
| `matter_event.py` | `EventType` | hearing, deposition, deadline, mediation, appointment, other |
| `discovery.py` | `DiscoveryRequestType` | interrogatory, rfa, rfp, witness_list |
| `discovery.py` | `DiscoveryRequestStatus` | pending_client, pending_review, finalized, objected |
| `discovery.py` | `RFASelection` | admit, deny, insufficient_information |
| `user_role.py` | `UserRoleType` | client, attorney, paralegal, admin |

---

## 9. FastAPI Route Conventions

- Routes are **thin** — they validate input, call a service method, and return a response. No business logic in route handlers.
- All routes are versioned: `/api/v1/...`
- Utility routes (health check, config): `GET /api/health`, `GET /api/config` — excluded from auth
- Auth routes (`/api/v1/auth/me`, `/api/v1/auth/correlate-staff`) require a valid JWT but NOT `require_role()`
- All other routes use `Depends(require_role([...]))` for RBAC
- Return Pydantic response schemas (from `app/schemas/`) — never return raw `InDB` models directly to the frontend

### Middleware Stack (in order)
1. `CORSMiddleware` — localhost origins in dev; `host_url` only in prod
2. `AuthMiddleware` — validates Supabase JWT via JWKS (ES256); injects `supabase_uid`, `role`, `email` into `request.state`; excluded paths: `/api/health`, `/api/config`, `/docs`, `/openapi.json`, `/redoc`; passes through `OPTIONS` preflight
3. Route-level `Depends(require_role([...]))` — resolves authoritative role from `user_roles` table, not JWT claim

---

## 10. Authentication & Correlation Flow

### How It Works

- Frontend auth uses Supabase JS (Google OAuth). All data access goes through FastAPI.
- `user_roles` is the **auth entry point**. It has `supabase_uid` (nullable) and `auth_email` (nullable). Login lookup: `user_roles WHERE supabase_uid = <jwt sub>` — single query.
- `staff.supabase_uid` is also nullable and gets populated during correlation, but is **not** the auth lookup path.
- `GET /api/v1/auth/me` returns **404** when no role is found. The frontend treats 404 as "needs correlation" and redirects to `/onboarding`.
- `POST /api/v1/auth/correlate-staff` matches `user_roles.auth_email` to the JWT email (where `supabase_uid IS NULL`), then writes `supabase_uid` into both `user_roles` and `staff`. It is idempotent.
- `auth_flow.py` routes do NOT use `require_role()` — any authenticated user can access them.
- Read `auth_flow.py`, `AuthCallbackPage.tsx`, and `OnboardingPage.tsx` for the actual implementation.

---

## 11. LLM Service (`app/services/llm_service.py`)

All LLM calls go through a single `LLMService` class. No other file in the codebase imports an LLM SDK directly.

- `complete(system_prompt, user_message)` → dispatches to `settings.llm_vendor`
- `complete_fast(system_prompt, user_message)` → dispatches to `settings.llm_fast_vendor`
- Supported vendors: `anthropic`, `gemini`, `openai`, `groq`, `deepseek`
- Lazy imports per vendor (avoids loading unused SDKs)
- Always log at `DEBUG` level before and after LLM calls (prompt truncated to 200 chars if needed)
- LLM responses that should be structured data must instruct the model to return **only valid JSON with no markdown fences or preamble**; parse with `model.model_validate_json(response_text)`

### Key LLM Use Cases

| Feature | Endpoint | Notes |
|---------|----------|-------|
| Natural language billing entry | `POST /api/v1/billing/parse` | Returns preview card; not committed until attorney confirms |
| Discovery request ingestion | `POST /api/v1/discovery/ingest` | Segments and classifies raw discovery text |
| (Future) Statement parsing | `POST /api/v1/inventory/parse-statement` | Extracts asset/debt details from uploaded PDF |

---

## 12. Business Logic: Billing

### Rate Resolution Order (BillingService.resolve_rate)

1. **Pro bono short-circuit:** if `matter.is_pro_bono` is True → rate=0, amount=0 (returns immediately)
2. `matter_rate_overrides` — per-staff, per-matter override
3. `matter.rate_card` — rates by role (e.g. `{"attorney": 350, "paralegal": 150}`)
4. `staff.default_billing_rate` — staff member's default rate

This is enforced in three places:
- Python: `BillingService.resolve_rate()` (primary)
- SQL function: `resolve_billing_rate()` (for reporting queries)
- DB trigger: `enforce_pro_bono_zero_rate` (backstop on INSERT/UPDATE)

### Immutability Rules
- Billed entries cannot be edited — `prevent_billed_entry_edit` trigger
- Trust ledger entries cannot be updated or deleted — `deny_trust_ledger_mutation` trigger
- Audit log entries cannot be updated or deleted — `deny_audit_log_mutation` trigger

---

## 13. Audit Log

Sensitive actions must write a record to the `audit_log` table **in addition to** the application log. Use the `AuditLogger` service — do not write to `audit_log` directly from route handlers.

`AuditLogger.log()` **never re-raises on failure** — audit logging must not crash the primary operation.

Actions requiring an audit log entry:
- Billing entry created / edited / deleted
- Billing cycle closed
- Bill sent to client
- Fee agreement signed
- Trust ledger transaction posted
- User role changed

---

## 14. Frontend Conventions

### API Calls
All backend calls go through `src/lib/api.ts`. Never call `fetch()` or `axios` directly from a component.

```typescript
import { apiFetch } from '../lib/api'
const data = await apiFetch<MyType>('/api/v1/some-endpoint')
```

`apiFetch<T>()` automatically attaches the Supabase Bearer token from the current session.

### Supabase JS Client
Used **only** for auth session management (`src/lib/supabaseClient.ts`). Do not use `supabase.from(...)` for data queries from the frontend.

### Routing
- Public routes: `/`, `/login`, `/auth/callback`, `/onboarding`, `/access-denied`
- Protected routes: `/app/*` wrapped in `ProtectedRoute` → `AppShell`
- `ProtectedRoute` redirects to `/login` (no session), `/onboarding` (no role), or `/access-denied` (client role)
- Admin-only nav items (e.g. Admin page) filtered by role in `AppShell`

### Dual-Density Layout
`AuthContext` sets `document.body.dataset.density` based on the user's role:
- `data-density="relaxed"` — client portal (generous spacing, larger type)
- `data-density="compact"` — staff portal (tighter grid, more info per viewport)

### Styling
- Tailwind CSS utility classes only — no inline styles
- Custom component classes defined in `src/index.css` `@layer components`: `btn-primary`, `btn-secondary`, `btn-gold`, `card`, `input`, `label`
- Custom colors in `tailwind.config.js`: navy, gold, off-white, text-primary, text-secondary, border
- Fonts: `font-display` (Playfair Display), `font-sans` (Inter), `font-mono` (JetBrains Mono)

---

## 15. Docker

### Running with Docker
```bash
docker compose up              # Dev (override auto-applied): hot reload, DEBUG logging
docker compose -f docker-compose.yml up  # Production: static nginx + uvicorn
```

### Running without Docker
```bash
# Terminal 1 — backend
uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

Vite dev server (port 3000) proxies `/api` to `http://localhost:8000`.

### Production Frontend
Multi-stage Docker build: `npm run build` → static bundle served by nginx. Nginx config handles SPA fallback (`try_files $uri $uri/ /index.html`) and proxies `/api/` to the backend service.

### Startup Checks
1. Pydantic validates all `Settings` fields — raises `ValidationError` before accepting requests
2. `SupabaseManager.__init__` raises `ValueError` if `supabase_url` or `supabase_service_role_key` are empty
3. Logs at `INFO`: `"Cyclone API started | env=%s llm_vendor=%s log_level=%s"`

---

## 16. Implementation Status — What's Not Built Yet

| Feature | Status | Notes |
|---------|--------|-------|
| Client Portal (separate from staff) | Not started | Client role exists; no client-facing pages |
| PDF bill generation | Not started | WeasyPrint in requirements.txt; no `pdf_service.py` |
| Stripe checkout / webhooks | Not started | Keys configured; no handler |
| Email notifications | Not started | No email service |
| Fee agreement templates + e-sign | Not started | Model exists; no UI or workflow |
| Discovery response editing UI | Partial | Request viewing works; response CRUD not wired |
| Client intake form / StepWizard | Not started | |
| File upload (receipts, documents) | Not started | |
| Shared components (DataTable, ConfirmDialog) | Not started | Pages use inline tables |
| Test suite | Not started | No unit or integration tests |
| Phase 2 conflict checking (pg_trgm) | SQL ready | Python wiring uses substring match only |

---

## 17. Environment Variables

| Variable | Used By | Notes |
|----------|---------|-------|
| `FIRM_NAME` | Backend | Displayed in `/api/config` |
| `IS_DEVELOPMENT` | Backend | Gates debug behavior and docs |
| `HOST_URL` | Backend | CORS allowed origin |
| `SUPABASE_URL` | Backend + Frontend | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend only | Never expose to frontend |
| `SUPABASE_ANON_KEY` | Frontend only | Subject to RLS |
| `SUPABASE_JWT_SECRET` | Backend | **Currently unused** — middleware uses JWKS/ES256 instead |
| `LLM_VENDOR` | Backend | Active vendor: gemini, openai, anthropic, groq, deepseek |
| `LLM_FAST_VENDOR` | Backend | Vendor for latency-sensitive calls |
| `ANTHROPIC_API_KEY` | Backend | If vendor = anthropic |
| `GEMINI_API_KEY` | Backend | If vendor = gemini |
| `OPENAI_API_KEY` | Backend | If vendor = openai |
| `GROQ_API_KEY` | Backend | If vendor = groq |
| `DEEPSEEK_API_KEY` | Backend | If vendor = deepseek |
| `STRIPE_SECRET_KEY` | Backend | Never expose to frontend |
| `STRIPE_PUBLISHABLE_KEY` | Backend → `/api/config` → Frontend | |
| `STRIPE_WEBHOOK_SECRET` | Backend | Webhook validation |
| `LOG_LEVEL` | Backend | Default: WARNING |
| `VITE_SUPABASE_URL` | Frontend build | Baked in at build time |
| `VITE_SUPABASE_ANON_KEY` | Frontend build | |
| `VITE_API_BASE_URL` | Frontend build | |

`.env.example` must be kept current. Every new env var must appear in `.env.example` in the same commit.

---

## 18. What NOT to Do

| Don't | Do Instead |
|-------|-----------|
| Use `from app.xxx import` in backend code | Use relative imports: `from util.settings import settings` |
| Call `logging.getLogger()` directly | `LoggerFactory.create_logger(__name__)` |
| Instantiate `Settings()` in app code | `from util.settings import settings` |
| Call `supabase.create_client()` outside `supabasemanager.py` | Use a repository class |
| Pass a JSON string to `repo.insert()` | Pass `.model_dump()` (a dict) |
| Write business logic in route handlers | Write it in a service class |
| Query Supabase tables from React components | Go through `src/lib/api.ts` → FastAPI |
| Use `orm_mode = True` | Use `ConfigDict(from_attributes=True)` |
| Log PII (names, amounts, case facts) | Log entity IDs only |
| Import LLM SDKs outside `llm_service.py` | Call `llm_service.complete(...)` |
| Hardcode environment-specific values | Use `settings.*` |
| Use `supabase.from(...)` in frontend components | Use `apiFetch()` from `src/lib/api.ts` |

---

*End of CLAUDE.md — keep this file current as conventions evolve.*

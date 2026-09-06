# CLAUDE.md — Cyclone Project Conventions
> This file is read by Claude Code at the start of every session.
> Follow every rule here without exception unless the developer explicitly overrides one in the current session.

---

## 1. Project Overview

**Cyclone** is a legal practice management platform with a React frontend and FastAPI backend, persisted to Supabase (PostgreSQL + Storage). The full spec lives in `CYCLONE_PRD.md` at the repo root. Read it before beginning any non-trivial feature work.

**Repo:** https://github.com/tjdaley/cyclone.git  
**Stack:** React 18 (Vite) · FastAPI · Supabase (PostgreSQL + Storage) · Docker  
**Python:** 3.11+  
**Node:** 20+  
**Status:** Active development. Core staff-portal features built: matters, clients, billing (with NL parse), discovery ingestion + editing + Word export, pleading ingestion + claims extraction. Client portal, Stripe, and PDF bill generation are not yet built. See §16 for the full status list.

---

## 2. Repository Layout

This tree describes the **patterns and conventions** — not an exhaustive file list. When you need to know exactly which files exist, use Glob/Read. Files and methods drift; patterns do not.

```txt
cyclone/
├── app/                           # FastAPI backend
│   ├── main.py                    # App factory, middleware, router registration
│   ├── dependencies.py            # get_db_manager, get_current_user, require_role
│   ├── Dockerfile                 # Python 3.11-slim
│   ├── requirements.txt           # pip dependencies
│   ├── config/
│   │   └── llm_profiles.json      # Task-named LLM profile catalog — see §11
│   ├── crm_worker.py              # Background worker: jobs every few seconds, CRM polls on their own cadence — see §11a
│   ├── util/
│   │   ├── settings.py            # Settings(BaseSettings) singleton — see §5
│   │   ├── llm_profiles.py        # Loads/resolves the profile catalog — see §11
│   │   ├── schema_check.py        # Startup diff of live columns vs models — see §15
│   │   ├── redis_client.py        # claim_once / lock — fleet coordination
│   │   └── loggerfactory.py       # LoggerFactory — see §6
│   ├── middleware/
│   │   └── auth_middleware.py     # JWT validation via JWKS (ES256); injects uid/role/email
│   ├── db/
│   │   ├── models/                # Pydantic domain + InDB models — see §8
│   │   │                          # One file per domain: staff, client, matter, billing_*,
│   │   │                          #   trust_ledger, fee_agreement, matter_event, discovery,
│   │   │                          #   pleading, user_role, audit_log
│   │   └── repositories/          # Extensions of BaseRepository[T] subclasses for each table — see §7
│   ├── routers/                   # FastAPI route handlers (thin — delegate to services)
│   │                              # Each router is prefixed /api/v1/<domain>
│   ├── services/                  # Business logic, LLM/PDF/Storage calls — see §11
│   │   ├── llm_service.py         # Multi-vendor LLM dispatch (complete, complete_fast, complete_with_image)
│   │   ├── pdf_service.py         # PDF text extraction (PyMuPDF + LLM vision fallback)
│   │   ├── storage_service.py     # Supabase Storage wrapper for matter documents
│   │   ├── docx_service.py        # Word document generation for discovery responses
│   │   ├── billing_service.py     # Rate resolution, pro bono, NL parse, balance, cycle close
│   │   ├── discovery_service.py   # Discovery document classification + item extraction
│   │   ├── pleading_service.py    # Pleading preview/commit orchestration
│   │   ├── intake_service.py      # Open a client + matter from a filed pleading — see §11
│   │   ├── job_service.py         # Queue/run background work — see §11a
│   │   ├── lead_service.py        # CRM leads; promotion to client + matter
│   │   ├── conflict_service.py    # Phase 1 substring match; Phase 2 pg_trgm ready
│   │   └── audit_logger.py        # AuditLogger.log() — never re-raises on failure
│   └── schemas/                   # Pydantic request/response schemas (one per domain)
├── frontend/                      # React + Vite + TypeScript + Tailwind CSS
│   ├── Dockerfile                 # Multi-stage: node build → nginx
│   ├── nginx.conf                 # SPA fallback + /api proxy with timeouts for LLM endpoints
│   ├── package.json               # react@18, react-router-dom@6, @supabase/supabase-js@2
│   ├── vite.config.ts             # Port 3000, /api proxy to localhost:8000
│   ├── tailwind.config.js         # Custom colors: navy, gold, off-white, success, warning, danger
│   └── src/
│       ├── main.tsx               # React 18 entry point
│       ├── App.tsx                # BrowserRouter, public + protected routes
│       ├── index.css              # Tailwind directives + component layer (btn-*, card, input, label)
│       ├── context/AuthContext.tsx # Session, profile, density, refreshProfile, signOut
│       ├── lib/
│       │   ├── supabaseClient.ts  # Auth only — never use for data queries
│       │   └── api.ts             # apiFetch<T>() with Bearer token injection + typed wrappers
│       ├── types/                 # Shared TypeScript interfaces mirroring backend schemas
│       │                          # common, auth, client, matter, staff, billing, discovery, pleading
│       ├── components/
│       │   ├── ProtectedRoute.tsx
│       │   └── AppShell.tsx       # Sidebar nav, role-gated menu items
│       └── pages/
│           ├── LandingPage.tsx
│           ├── LoginPage.tsx
│           ├── AuthCallbackPage.tsx
│           ├── OnboardingPage.tsx
│           ├── AccessDeniedPage.tsx
│           ├── PrivacyPolicyPage.tsx    # For Google OAuth verification
│           ├── TermsOfUsePage.tsx       # For Google OAuth verification
│           └── app/                     # Protected routes under /app/*
│               ├── DashboardPage.tsx
│               ├── BillingPage.tsx
│               ├── MattersPage.tsx
│               ├── ClientsPage.tsx
│               ├── DiscoveryPage.tsx
│               ├── PleadingsPage.tsx
│               ├── MatterDetailPage.tsx   # /app/matters/:matterId — parties, staff, children, counsel, claims
│               ├── MatterIntakePanel.tsx  # Review a dropped pleading → new client + matter
│               ├── LeadPromotePanel.tsx   # Promote a lead → client + matter (optional pleading)
│               ├── TagManager.tsx          # Create/edit/retire matter tags; firm tags read-only
│               ├── UndisclosedAccountsPanel.tsx  # Accounts referenced but never produced
│               └── AdminPage.tsx
├── db/
│   └── migrations/                # SQL DDL — run in numeric order
│       ├── 001_extensions.sql     # pg_trgm, pgcrypto
│       ├── 002_tables.sql         # Core tables
│       ├── 003_indexes_triggers.sql
│       ├── 004_functions.sql      # search_conflicts, resolve_billing_rate, views
│       ├── 005_rls.sql            # Row-Level Security policies
│       ├── 006_staff_auth_fields.sql
│       ├── 007_discovery_redesign.sql      # discovery_requests → parent + items split
│       ├── 008_discovery_item_editing.sql  # response column + standard_privileges/objections
│       ├── 009_pleadings_and_oc.sql        # pleadings, claims, opposing_counsel, children
│       ├── 010–014                         # CRM: leads_workflow, lead_actions, KB, agent runs
│       ├── 015_pleading_status.sql         # matter_pleadings.status (live/superseded/withdrawn/inactive)
│       ├── 016_prior_counsel_role.sql      # widens the matter_opposing_counsel role CHECK
│       ├── 017_discovery_client_instructions.sql
│       ├── 018_jobs.sql                    # background job queue — see §11a
│       ├── 019_clients_referred_to_staff.sql
│       ├── 020_clients_schema_reconcile.sql  # brings 002 back in line with the deployed table
│       ├── 021–022                         # client user_roles; discovery composite FK
│       ├── 023_financial_statements.sql    # accounts, statements, transactions
│       ├── 024_transaction_categories_and_tags.sql  # FIS chart + Rule 1006 tags
│       ├── 025_job_params.sql             # jobs.params jsonb — options a job was started with
│       ├── 026_account_ownership_and_merge.sql  # joint accounts; ON UPDATE CASCADE for merges
│       ├── 027_purge_rejected_statements.sql   # ⚠ deletes data — old soft-rejected rows
│       ├── 028_transaction_soft_delete.sql    # deleted_at on transactions
│       ├── 029_transaction_check_number.sql  # check_number on transactions
│       ├── 030_matter_caption.sql          # matters.case_style, matters.client_alignment
│       ├── 031_fis_category_settings.sql  # payment schedules per category, per person
│       ├── 032_category_rules.sql        # keyword rules + category provenance on transactions
│       ├── 033_creditor_discovery.sql    # categories.is_liability + payee classifications
│       └── run_all.sql                     # NOTE: only includes 001–005; later files are run by hand
├── docker-compose.yml             # Production: tagged images, frontend on :8094 behind haproxy
├── docker-compose.override.yml    # Dev: hot reload, DEBUG logging, ports 3000/8000
├── .env.example                   # All keys, no values — committed
├── CYCLONE_PRD.md                 # Full product spec with implementation status
└── CLAUDE.md                      # This file
```

---

## 3. Golden Rules

These override everything else, including your own judgment about "better" patterns.

1. **Read before you write.** Before editing any existing module, read it fully. Never assume file contents match what you expect. Never trust documentation (including this file) over the actual source.
2. **No new patterns without justification.** If the codebase already has a pattern for something (logging, DB access, settings), use it. Do not introduce a second way to do the same thing.
3. **No raw SQL from the frontend.** All data mutations and queries go through FastAPI endpoints.
4. **No new `Settings()` instantiations.** Import `settings` from `util.settings` everywhere.
5. **No direct `logging.getLogger()` calls.** Use `LoggerFactory.create_logger(__name__)` exclusively.
6. **No LLM calls outside `app/services/llm_service.py`.** All AI completions are dispatched through the `LLMService` singleton.
7. **No `supabase.create_client()`.** All DB access goes through repository classes.
8. **No direct Supabase Storage access outside `storage_service.py`.** All file uploads/downloads go through `StorageService`.
9. **No per-page type definitions.** Shared types live in `frontend/src/types/`. API functions in `api.ts` return typed promises — no `as Type[]` casts at call sites.
10. **Test before declaring done.** Run the relevant test suite or perform a manual smoke test against the dev Docker stack before marking a task complete.

---

## 4. Import Paths — CRITICAL

The backend runs from inside the `app/` directory:

```bash
uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000 --reload
```

**All Python imports use relative paths (no `app.` prefix):**

```python
# ✅ Correct
from util.settings import settings
from util.loggerfactory import LoggerFactory
from db_handler import SupabaseManager, DatabaseManager
from db.models.staff import StaffMember, StaffMemberInDB
from db.repositories.staff import StaffRepository
from middleware.auth_middleware import AuthMiddleware
from services.billing_service import BillingService
from schemas.common import MessageResponse

# ❌ Wrong
from app.util.settings import settings
```

---

## 5. Settings (`app/util/settings.py`)

- `Settings(BaseSettings)` is a Pydantic settings class loaded from `.env`
- The module-level singleton `settings = Settings()` is the **only** instance used throughout the app - Import pattern: `from util.settings import settings`
- **Adding new fields:** Add to the `Settings` class with a safe default (usually `""` or `None`); add the key to `.env.example` in the same PR
- **Never hardcode** API keys, URLs, or environment-specific values anywhere in application code

### Key fields

| Field | Type | Purpose |
| ----- | ---- | ------- |
| `version` | `str` | API version string |
| `id` | `str` | Deployment identity — which server instance answered (e.g. `DEV`, `ec2-54-84-177-70`) |
| `host_url` | `str` | CORS and URL generation |
| `is_development` | `bool` | Gates debug behavior and docs endpoints |
| `firm_name` | `str` | Displayed in config endpoint |
| `supabase_url` | `str` | Supabase project URL |
| `supabase_service_role_key` | `str` | Used by backend only — never expose to frontend |
| `supabase_jwt_secret` | `str` | **Currently unused** — auth middleware uses JWKS/ES256 instead |
| `supabase_anon_key` | `str` | Used by frontend Supabase JS client |
| `llm_profiles_file` | `str` | Path to the task-profile catalog; relative paths resolve against `app/` (see §11) |
| `llm_temperature`, `llm_top_p`, `llm_max_tokens` | `float`/`int` | Global sampling defaults; profiles and candidates may override |
| `llm_timeout_seconds` | `float` | Per-call ceiling. Without it a hung vendor hangs the request and failover never fires. Keep ≥10 — Gemini rejects shorter deadlines |
| `{vendor}_api_key`, `{vendor}_base_url` | `str` | Per-vendor credentials only — no model names |
| `job_poll_interval_seconds` | `int` | How often the worker looks for queued jobs (see §11a). Short — someone is watching a spinner |
| `referral_types` | `list[str]` | Client intake referral type dropdown values |
| `time_increment_options` | `list[float]` | Valid billing time increments |
| `default_refresh_trigger_pct` | `float` | Default retainer refresh threshold |
| `stripe_*` | `str` | Stripe keys (publishable is safe to expose via `/api/config`) |
| `log_level`, `log_format` | `str` | Logging configuration |

---

## 6. Logging (`app/util/loggerfactory.py`)

### Usage — Required Pattern

```python
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)
```

**Do not** call `logging.getLogger()` directly anywhere in application code.

### Rules

- Use `%s` format args in all log calls — never f-strings:

  ```python
  LOGGER.info("Billing entry committed: entry_id=%s matter_id=%s", entry_id, matter_id)  # ✅
  LOGGER.info(f"Billing entry committed: entry_id={entry_id}")                           # ❌
  ```

- **No PII in log messages.** Never log client names, financial amounts, SSNs, case facts, or attorney-client communications. Reference records by database ID only.
- Log level defaults to `settings.log_level` unless overridden at logger creation.
- `LoggerFactory` sets `propagate = False` — do not set it again.

### Log Levels

| Level | Use For |
| ----- | ------- |
| `DEBUG` | LLM prompt/response text, raw DB query params — dev only |
| `INFO` | Request received, record committed, bill generated, user authenticated |
| `WARNING` | LLM parse failure, Stripe webhook mismatch, conflict check error |
| `ERROR` | DB operation failed, unhandled exception, LLM call failure |
| `CRITICAL` | System cannot start, missing required config |

---

## 7. Database Access (`db_handler package`)

### Architecture

```txt
SupabaseManager(DatabaseManager)   ← concrete implementation (from db_handler package)
        ↑ used by
BaseRepository[T]                  ← generic CRUD base (from db_handler package)
        ↑ inherited by
Domain repositories                ← one per entity (implemented in this project)
        ↑ instantiated in
Route handlers (via Depends())     ← or services injected from handlers
```

### SupabaseManager Rules

- `SupabaseManager` uses `supabase_service_role_key` — it **bypasses Supabase RLS**. Access control is enforced at the FastAPI route layer via `require_role()`.
- All methods retry 3 times on `APIError` with exponential backoff (2–10s).
- `select_one` returns `None` on PGRST116 (no row found) — callers must handle `None`.
- `insert()` will raise `ValueError` if `data` is a string. Always pass a `dict` (`model.model_dump()`).
- `insert()` and `update()` both pass data through `_json_safe()` which converts `datetime`, `date`, and `Enum` values to JSON-serializable types before httpx serialization. This means you can pass `model_dump()` directly without worrying about serialization.
- `update()` matches on the `id` field by default. It updates the entire record. There is no field-based update in the base class.
- `exists()` is a count-only check — use it for duplicate guards; do not use `select_one` for that purpose.

### Repository pattern

```python
# app/db/repositories/my_entity.py
from db_handler import BaseRepository, DatabaseManager
from db.models.my_entity import MyEntityInDB

class MyEntityRepository(BaseRepository[MyEntityInDB]):
    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "my_entities", MyEntityInDB)

    def get_by_matter(self, matter_id: int) -> list[MyEntityInDB]:
        return self.select_many(condition={"matter_id": matter_id})[0]
```

Instantiate in route handlers via `Depends(get_db_manager)`. Don't cache repositories module-level — each request gets a fresh manager.

---

## 8. Model Conventions (`app/db/models/`)

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
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)
```

### Rules

- Every field must have `description=` in `Field()`
- `InDB` models always add `id`, `created_at`, `updated_at` — nothing else
- **Exception:** `TrustLedgerEntry` and `AuditLog` are immutable — their `InDB` models have no `updated_at`
- **Exception:** `AuditLog` has `id: str` (UUID) rather than `int`
- `model_config = ConfigDict(from_attributes=True)` goes on `InDB` only
- Do not use `orm_mode = True` (deprecated Pydantic v1 syntax)
- All person-name fields use `FullName` from `db/models/staff.py` (`{courtesy_title, first_name, middle_name, last_name, suffix}`)

---

## 9. FastAPI Route Conventions

- Routes are **thin** — they validate input, call a service method, and return a response. No business logic in route handlers.
- All routes are versioned: `/api/v1/...`
- Utility routes (`GET /api/health`, `GET /api/config`) are excluded from auth entirely
- Auth routes (`/api/v1/auth/me`, `/api/v1/auth/correlate-staff`) require a valid JWT but NOT `require_role()`
- All other routes use `Depends(require_role([...]))` for RBAC
- Return Pydantic response schemas (from `app/schemas/`) — never return raw `InDB` models directly to the frontend
- File uploads use `UploadFile = File(...)` + `Form(...)` for metadata

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

## 11. Services Layer (`app/services/`)

All business logic, LLM calls, PDF extraction, Word generation, and Supabase Storage access live in services — never in route handlers.

### LLM Service (`llm_service.py`) + Profile Catalog (`util/llm_profiles.py`)

**Call sites name a task, never a vendor or a model.** Which models serve a task is config, not code.

```python
llm_service.complete(_METADATA_SYSTEM, prompt_text, profile="analyze_pleading")
llm_service.complete_with_image(system, msg, b64, mime, profile="ocr_document_page")
```

The catalog is `app/config/llm_profiles.json` (path from `settings.llm_profiles_file`, resolved against the `app/` package so the same relative path works in Docker and locally). An entry takes one of three forms:

```jsonc
"fast": [ {"vendor": "gemini", "model": "gemini-3.1-flash-lite-preview"} ],   // chain
"explain_message_edit": "fast",                                              // alias
"response_guardrail": { "extends": "fast", "temperature": 0.0,               // object
                        "description": "Safety check on a drafted reply" }
```

- Object keys: `description`, `extends`, `chain`, `temperature`, `top_p`, `max_tokens`, `timeout_seconds`, `vision`. Unknown keys are rejected at load. Top-level keys starting with `_` are ignored — that's how you comment in JSON.
- **Convention:** `default`, `fast`, and `vision` are the physical model chains; every task profile `extends` one of them. Retune a task by changing what it extends.
- **A deadline too short for the work selects for the model that gives up soonest.** This is the non-obvious failure and it cost a session to see. At the global 90s, a 27-page statement extraction "succeeded" only because that model stopped at 38 of 286 transactions — a short answer is the only kind that fits — while the run where all three vendors tried to finish was killed three times over. `timeout_seconds` on a profile fixes it where the answer is long: `extract_account_statement` 300s, `ocr_document_page` 180s, everything else on the global 90s. Both run inside background jobs, where a generous deadline costs only patience.
- **Gemini enforces the deadline server-side**, so an overrun arrives as a structured `504 DEADLINE_EXCEEDED` from Google rather than a client timeout. Anthropic and OpenAI raise client-side (`APITimeoutError`). Same event, three different shapes — do not read the Gemini 504 as a network fault.
- **Chain order is measured, not priced.** The same 27-page, 286-transaction statement was run through each vendor by hand: Claude and OpenAI complete and correct, Gemini good but stopped at 200 entries, DeepSeek lost and mis-stated amounts and invented JSON keys. `default` and `vision` therefore run Claude → OpenAI → Gemini. **DeepSeek is in no chain and must not be added to one that touches evidence.**
- **`complete_detailed()` returns `LLMResult`** — the text plus the vendor, model, and how many candidates were tried. Use it wherever the answer becomes part of a record that has to say how it was produced. `complete()` still returns a bare string and is right everywhere else. The profile name says what was *asked for*; it cannot say who answered, and on a long statement read in passes it cannot even be one answer — the chain is walked independently per call, so a single document can be part Claude and part OpenAI.
- **Vendor SDKs drift, and the failover hides it.** Any exception fails the candidate over with a `WARNING` — including a crash in *our own* parsing, which then reads as "the vendor refused". Two of these ran for a whole session: `response.content[0].text` on Anthropic breaks the moment a model returns extended thinking, because the opening block is a `ThinkingBlock` with no `text` (use `_anthropic_text`, which selects blocks by type); and OpenAI's `chat.completions` no longer accepts `max_tokens`. **When one vendor is always serving, suspect the ones ahead of it in the chain rather than assuming they are down.**
- **Failover:** the first candidate is tried; on *any* failure — network, quota, auth, API error, or an empty response — the service logs a `WARNING` and moves to the next candidate. No same-candidate retry. Chain exhausted → `LLMUnavailableError` (a `RuntimeError`).
- **An unknown profile name raises `LLMUnavailableError`** listing the known names. Profile names are config; a typo must not silently run on some other model.
- Candidates whose vendor has no API key, or that can't serve the call type (vision), are **skipped without being called**.
- Sampling resolves **candidate → profile → global `llm_*` setting**.
- The catalog loads at import. A missing or malformed file raises `LLMProfileCatalogError` and the process does not start. When `is_development`, the file's mtime is checked per lookup and edits reload without a restart; a reload failure keeps the previous catalog.
- `describe_profiles()` / `validate_profiles()` run at startup to log resolved chains and warn about unknown vendors, missing keys, and non-vision vendors in a vision profile.
- Supported vendors: `anthropic`, `gemini`, `openai`, `groq`, `deepseek`. Vision supported on Anthropic, Gemini, and OpenAI.
- Lazy imports per vendor (avoids loading unused SDKs)
- Log at `DEBUG` before and after LLM calls — format string `"%.*s"` truncates to `_MAX_LOG_CHARS`
- LLM JSON responses often come wrapped in ` ```json ``` ` fences despite the prompt saying "no markdown". Services that parse LLM JSON must strip fences before `json.loads()` (see `discovery_service._strip_markdown_fences`).

### PDF Service (`pdf_service.py`)

- `extract_text(pdf_bytes)` — PyMuPDF for searchable pages; LLM vision fallback for image-only pages
- Image enhancement: grayscale, contrast 2.0, sharpness 1.5 before sending to the vision model
- Tesseract is **not** used — LLM vision is more accurate for legal documents
- **A page nobody could read is not a blank page.** When the text layer is unusable *and* the vision fallback fails, `_vision_extract` leaves `_UNREADABLE_MARKER` in place of the page's text instead of returning `""`. It used to return an empty string, so the page contributed nothing and no record anywhere said one had been lost. The marker travels in `raw_text`, and `statement_service` raises `PAGE_UNREADABLE` (warn) naming the pages.
- **Output is sanitized** (`_sanitize`): control characters and lone surrogates are stripped. A NUL from a badly encoded exhibit page makes Postgres reject the entire row (`22P05`, "unsupported Unicode escape sequence"), which fails an ingest *after* all the extraction work is done. Never persist raw extraction output that has not been through this.
- Logs pages, OCR-page count, and elapsed time at `INFO` — OCR is the dominant cost of an ingest and a slow upload otherwise looks like a hang

### Storage Service (`storage_service.py`)

- Wraps Supabase Storage with matter-scoped paths: `matters/{matter_id}/pleadings/{id}.pdf` and `matters/{matter_id}/discovery/{id}.pdf`
- Intake uploads land at `intake/{job_id}.pdf` — at intake there is no matter to file under yet
- Bucket name: `matter-documents` (private, signed URLs only)
- `upload_pleading`, `upload_discovery`, `upload_intake`, `get_signed_url`, `download`, `move`, `delete`
- The bucket must be created manually in the Supabase dashboard before use
- Serving a stored file to the SPA: return a **signed URL as JSON** and let the browser navigate to it (`GET /pleadings/{id}/pdf-url`). A redirect or a plain link cannot carry the bearer token.

### Docx Service (`docx_service.py`)

- `generate_discovery_response_docx(document_type, matter_name, items)` → bytes
- Parses markdown (`**bold**`, `*italic*`, numbered/bulleted lists) into native Word runs
- **Line handling follows markdown block rules:** a single newline is a soft break inside the paragraph (`<w:br/>`, single-spaced); a blank line starts a new paragraph with `space_after`. This is what lets a witness block stay tight while blocks stay separated. The frontend preview uses `remark-breaks` so it agrees with the export.
- `instructions_to_client` is **never** exported — it is internal work product, not part of the response served on the other side

### Billing Service (`billing_service.py`)

Natural-language billing parse, rate resolution (see §12), billing cycle closure, client balance calculation.

### Discovery Service (`discovery_service.py`)

Two-step LLM pipeline for discovery document ingestion:

1. `classify_document(raw_text)` → metadata (type, propounded_by, service_date, response_days, look_back_date)
2. `extract_items(raw_text)` → list of numbered requests with verbatim `source_text` as markdown

### Pleading Service (`pleading_service.py`)

Stateless preview/commit pattern for pleading ingestion:

- `preview_ingest(matter_id, raw_text)` → `PleadingIngestPreviewResponse` (no writes; attorney reviews)
- `commit_ingest(staff_id, request)` → writes opposing parties **first** (everything else references them), then the pleading row, matter field updates, children, opposing counsel (bar-number dedup), and claims
- **Extraction needs to know who our client is.** `classify_and_extract(raw_text, client_name, firm_name)` takes them as required arguments: "opposing" is relative to our client, and a pleading filed against us names *our* attorney in its service paragraph. Without that context the model reasonably returns our own lawyer as opposing counsel.
- `clip_for_metadata(raw_text)` trims a document for a metadata call (40k head + 10k tail). Generous on purpose — the signature block, the only place a bar number appears, sits at the end of the *pleading*, not the end of the file, and a petition with a standing order attached runs well past ten pages.
- Committing an amendment marks what it amends `superseded`. A supplement does not — it adds to the live pleading.
- Children and opposing parties are deduplicated on commit, and the commit **re-reads the matter** rather than trusting the preview, so a stale preview or a double submit cannot duplicate.

### Intake Service (`intake_service.py`)

Opens a **client and matter** from a filed pleading — the case where nothing exists yet.

- `extract_case_style(raw_text)` takes no client context, deliberately: at intake we do not know which side we are on. The prompt asks for every party and every attorney *neutrally*, with `represents` on each attorney.
- The attorney answers "who do we represent?" once in the review screen; everything adverse is derived from that single answer, in `commit()` and nowhere else.
- `preview()` matches each party against existing **clients and leads**. Promoting a lead is preferred to creating a client from a caption — a lead has cleared the conflict check and carries contact details.
- `_match_confidence()` requires **first and last name to both agree**. Surname-only matching is not enough: adverse parties in a family-law caption almost always share a surname, so a looser rule offers the opposing spouse as a candidate for our own client. The frontend panels mirror this rule exactly — if you change one, change both.
- `commit()` creates the client (or reuses a matched one), then the matter, then delegates to `pleading_service.commit_ingest` so parties, counsel, children, and claims are written by the same code as every other pleading. `case=None` opens a client and matter with no pleading — that is how a lead is promoted off a phone call.

### Statement Service (`statement_service.py`)

Ingests bank, brokerage, and credit-card statements. Output is **evidence**, and three rules follow from that:

- **One sign convention.** An amount is signed by how it moves the balance the institution prints: a deposit and a card purchase are both positive, a withdrawal and a card payment both negative. Every account type then reconciles with `beginning + sum(amount) == ending`.
- **Every statement checks itself.** One that does not tie is stored as unreconciled with the exact delta. Nothing is invented to close the gap — a synthetic balancing row is precisely what gets cross-examined.
- **Inference is flagged.** `YEAR_INFERRED`, `LOCATION_INFERRED`, `SIGN_ASSUMED`, `AMOUNT_UNCLEAR`. A warn-level flag anywhere holds the whole statement in the exceptions queue; everything else is `auto_accepted` unseen, which is what makes a production of several hundred statements workable.

Money is `numeric(14,2)` everywhere and `Decimal` in Python. `_money()` converts via `str` on purpose — `Decimal(1203.02)` from a float carries binary rounding error into an exhibit. It crosses the wire as a **string** and the frontend formats it without ever calling `Number()`.

Statement extraction is the only caller of `pdf_service.extract_text(..., page_markers=True)`. The markers are what let the model report `physical_page_number`; a Rule 1006 summary has to cite the page, and a `bates_number` is copied from the page stamp or left null — never constructed from a neighbouring page.

**The institution is the field that goes wrong, and it is half the dedup key.** `find_match` keys on institution *plus* last four, so a misread name opens a second account for one that already exists — which is invisible once statements pile up as two half-complete histories. Two real misreads from one production: the name read as null (letterhead graphic), and the vision fallback answering **"CSI"** — Computer Services, Inc., the core-banking vendor whose imprint sits beside the form revision code at the foot of every page. `_FORM_VENDORS` rejects those answers outright, because an unnamed account is fixed in seconds while a confidently wrong one looks settled; `_INSTITUTION_WORDS` keeps the guard from eating a real "CSI Federal Credit Union". And `others_with_last4` raises `SAME_LAST4_DIFFERENT_INSTITUTION` (warn) when a new account duplicates a number the matter already holds — it reports rather than merging, because two banks really can share a last four. Covered by `tests/test_institution_misread.py`.

**The institution is often not in the text layer.** Many statements print the bank's name only inside the letterhead graphic. The page is otherwise dense with text, so it clears `_MIN_TEXT_LENGTH` and is never rendered for vision — the name exists solely as pixels nobody looks at, and the account is filed under "Unknown institution". That matters more than it sounds: institution plus last four is the dedup key, so the *next* upload of the same account opens a second row. `resolve_missing_institutions()` is the remedy — one narrow `pdf_service.ask_page()` vision call per unnamed statement, asking only that question.

**Merging is the repair when that has already happened.** `preview_merge()` reports before `merge()` acts, because a merge moves evidence and deletes a row. `PERIOD_OVERLAP`, `SAME_ACCOUNT`, and `DIFFERENT_MATTER` are blocking; `BATES_OVERLAP`, `LAST4_MISMATCH`, and `TYPE_MISMATCH` need `force`. Period overlap is blocking rather than forceable for a concrete reason: the unique index on `(account, period_start, period_end)` would reject the second statement and fail the merge part-way with an opaque database error. Two ordering rules inside `merge()`: statements move **before** the source is deleted (the statements table cascades on account delete, so deleting first destroys them), and successors naming the source as `antecedent_account_id` are repointed first. Covered by `tests/test_account_merge.py`.

**One missing date condemns every date in the batch.** A null is not merely a missing value — it marks a call that stopped reading the date column, and the dates that call *did* emit are no more trustworthy than the ones it dropped. Ground truth on a 27-page statement showed why: wrong dates never appeared in a batch that was otherwise clean, only ever mixed in with nulls, and they were undetectable on their own — inside the period, not reliably out of order. So `_date_audit_reason` triggers a re-read of the whole batch on a single null (or on any date outside the period, which is the only handle on a batch of wrong dates with no nulls).
- **Only the date column is re-read.** The amounts in those batches were perfect; re-running the batch to fix one column would re-roll the dice on data that already ties.
- **The re-read matches by POSITION.** Descriptions and amounts repeat constantly — three identical `Transfer from DDA (Sweep)` of $5,000.00 on one day is ordinary — so matching on them would date lines confidently and wrongly. Order is the key; description and amount only confirm.
- **The second reading wins, and every change is flagged** (`DATE_REREAD`, with both values). Dates do not enter reconciliation, so nothing downstream would otherwise reveal that a date had been revised.
- **One attempt, no looping.** Anything still undated raises `UNDATED_TRANSACTIONS` (warn) and a person dates it from the source page.
- **There is no ordering check, deliberately.** These statements list credits then debits, each section restarting at the beginning of the period, so dates jump backwards mid-batch on the first batch of nearly every statement. It would fire constantly and catch nothing known.
Covered by `tests/test_date_audit.py`.

**Checks in a summary table are transactions.** A block headed "CHECKS IN SERIAL NUMBER ORDER" is, on many statements, the *only* place a check appears — the prompt used to say it "only lists them again by number" and threw them away, which lost twelve debits worth $25,669.39 on one statement. Two things make it readable: the table is **column groups read left to right**, so the extracted text interleaves them and reading straight down a column gives the wrong rows; and a row whose amount is not a printed figure ("-See above-") is already itemised in the debit list, which is the bank telling you which rows would double-count. `_dedupe_checks` is the backstop for banks that reprint a check *with* its amount — the debit-list entry wins, because it carries the payee. A doubled debit is worse than a missing one: it reconciles to a wrong number rather than an obviously wrong one. Covered by `tests/test_check_extraction.py`.

**`check_number` is stored and searchable** (`checks_only`, or one number). A check is the only debit that does not say where the money went, and not every bank prints the images — so the number is what a discovery request asks about. Stored as text: leading zeros are kept, the footnote asterisk in "2493*" is stripped (it marks a gap in the serial run, not the number), and it is never arithmetic.

**A long statement must be read in passes.** One call reliably handles a short statement and reliably gives up on a long one. A 27-page statement of 286 entries came back with 38 — every credit, then fifteen debits, then a closing brace. The JSON was valid, the input was 36,863 characters against a 60,000 ceiling, and the output used about a seventh of its 32,000-token budget: nothing failed, nothing was truncated, the model just stopped. **No limit can be raised to fix that, because no limit was reached.** Above `_MAX_SINGLE_PASS_PAGES` the document is indexed once (accounts, periods, balances, page ranges) and then each statement is walked `_PAGES_PER_CHUNK` pages at a time asking only for transactions, so every answer is small enough that finishing is the easy option. Line numbers are assigned in Python — each pass only ever saw a slice — and every pass is handed the statement period, or a line printed "12/04" has no year.

**Check the extraction against what the statement says about itself.** The account summary states its own answer — "24 Deposits/Credits 202,100.41", "262 Checks/Debits 195,600.04" — and `_completeness_findings` compares both count and total per side, raising `INCOMPLETE_EXTRACTION` (warn). It reports a side only when the extraction falls **short** of what is printed, never when it exceeds it: a statement may split its debits across several named buckets — Bank of Texas prints "2 Checks & Withdrawals 159.11" and "Service Fees 2.00" separately — so extracting all three debits correctly reads as two too many against a single bucket. A shortfall has no innocent explanation; an excess usually does, and over-counting is caught by reconciliation instead. Reconciliation alone does catch a short read, but only as one unexplained delta; this says *which side* fell short and by how much, which is the difference between "something is wrong" and "the debits stopped after fifteen of two hundred sixty-two". Covered by `tests/test_long_statement.py`.

**An upload is surveyed before it is trusted, and the survey is advice.**
`survey_upload` reads the page count and counts pages whose text restarts at
"Page 1" — statements paginate themselves, so a document holding twenty-four
months says it twenty-four times. Past `_MANY_PAGES` (30) with no such signal,
length alone warns. It never blocks: a combined statement is one upload with
five accounts in it, and refusing that to catch a production would trade a real
workflow for a rare one. **The word boundary is the whole pattern** — `PAGE\s1`
also matches inside "Page 10", "Page 19" and "Page 142", so one long statement
reads as a dozen first pages and every long upload gets warned about;
`page\s+1(?!\d)` does not. Counted by **page**, not occurrence, or a
statement printing its pagination in both header and footer counts twice. The
survey uses the text layer with **no OCR fallback**, which is what lets it run
inside the upload request — a warning that had to render pages would cost more
than the mistake it prevents. It returns no warning rather than raising, because
the job is already queued by then and a failed warning must not read as a failed
upload. Written after a 142-page, twenty-four-month client production timed the
browser out, imported some statements, mangled the last, and had to be rejected
wholesale and split by hand. Covered by `tests/test_upload_survey.py`.

**A page is not always the unit of division.** Capital One 360 prints a combined
statement — every account the customer holds, one register straight after the
next, with no page break. A page therefore ends one account and begins another,
and on one real eight-page document page 5 carried the close of one register,
the whole of a second, and the opening of a third. Slicing by page handed the
pass reading account A a page ending with account B's opening balance, under a
context line reading *"these pages belong to"* A — a false statement the model
reasonably believed, filing B's first transactions against A on every account
after the first. So the index reports each statement's `header_text` and
`_statement_pages()` cuts the document at those headers **in Python**: the same
argument as Bates numbers and account numbers, since a header is a line the bank
prints and locating it is exact where judging where a register stops is not. A
statement beginning mid-page carries no page marker of its own, so one is
prepended from `_page_of_offset` — without it the first lines are attributed to
the *next* page, off by one exactly at the seam. No findable header falls back to
the page range and logs it. Covered by `tests/test_combined_statement.py`.

**A reply that stops mid-array is not a reply that is wrong.** The two failures
look identical from `json.loads` and want opposite responses, so they are told
apart by position: a decode error whose offset is the *end of the string* means
the parser consumed a complete value and then found nothing where a delimiter
had to be, which cannot happen to a complete answer. `UnparseableResponse`
carries the distinction, and **both kinds get a second ask** — what differs is
what the retry note says. Measured: two dense pages of a Capital One register
produced ~40 entries and 17,264 characters cut off mid-array, with `max_tokens`
at 32,000 and a fraction of it used. Nothing hit a ceiling; the reply simply
stopped.

**The retry is not a re-roll, and that is load-bearing.** This profile runs at
`temperature: 0.0`, so a second call on an identical prompt returns the
identical reply — the only thing making a retry worth anything is that the note
changes the question. Which is why the note has to name the failure it answers:
telling a model that ran out of room to escape its quotes more carefully
addresses nothing. Measured on the same document, a truncation-specific retry
recovered it **twice out of two**, and the reasoning that said it could not
("the answer comes back the same length") was wrong for exactly this reason.

Splitting is the fallback, not the first move: `_read_pages` halves the page
range and asks each half, down to a single page — the rule already stated for a
short extraction, *split the work instead* — and it is second because it costs a
call per half and the cheaper fix usually works. A page that still will not read
at width one is returned as unread rather than raised, so one bad page never
discards the half of a split that succeeded.

**One unparseable pass must not cost the whole document.** A chunk whose answer
was not valid JSON used to raise out of `extract()`, out of the worker, and fail
the job — so a five-account upload with four accounts read perfectly committed
nothing, and the person re-uploaded the same file with no reason to expect a
different result. Two changes. `_call_json` **asks again once**, telling the
model its previous answer was rejected: `llm_service` fails a candidate over on
an *exception* and deliberately never retries the same one, but a response that
arrived intact and merely will not parse is a different event, and only the
caller knows the answer had to parse. If the second attempt also fails, the
pages are recorded on the statement (`unread_pages`) and the walk continues;
commit raises `PASS_UNREADABLE` (warn), so the statement is held, says which
pages are missing, and will not reconcile — three independent signals that it is
short. The same trade `pdf_service` makes with its unreadable marker: a visible
partial loss beats a silent total one. **The response is never logged** — it is
a register of transactions, and §6 forbids amounts and payee names in logs; the
error position and response length distinguish a truncation from a bad escape,
and the text is recoverable from provenance.

**One statement can be read as two, and the duplicate guard cannot see it.** `find_period` is scoped to an *account*, so it only catches a repeat once both copies land on the same account. The failure that gets past it: a summary table — a DAILY ENDING BALANCE list, an account summary — is read as a second transaction register, and because the institution is usually unreadable on the same document (letterhead graphic), the phantom gets its own invented account. Two accounts, one period, no collision. Three defences, in order: the prompt names the blocks that are not registers and states that a repeated page header is not a new statement; `resolve_missing_institutions` re-reads *every* name off the page when one upload reports more than one institution, since disagreement means at least one is a guess; and `commit_document` tracks page ranges across the document and raises `SUSPECT_SPLIT` (warn) when two statements overlap by more than a single
boundary page, or share one while naming different institutions. **A shared page
is not by itself wrong** — the flag first shipped saying "two statements cannot
be printed on the same page", which is false for a combined statement and put
every account on such an upload into the exceptions queue. A real seam touches
exactly one page and both accounts name the same bank, because a combined
statement is one bank printing its own accounts; a phantom register invents an
account whose institution differs or could not be read, which is the same signal
`resolve_missing_institutions` already acts on. Covered by `tests/test_statement_split_guard.py`.

**The three deletes are deliberately not the same delete.** Nothing in this database is the original record — the Bates-stamped PDF in Storage is — so a statement or an account is removed outright: a mistake costs a re-import, and a half-deleted account sitting in an inventory is worse than one that is gone. A single *line* is different, and gets a soft delete:

- Dropping a line asserts something about the document ("this is not printed there") and changes whether the statement reconciles, so it is flagged, hidden, attributed, and re-reconciled — never destroyed. `delete_transaction` / `restore_transaction`, swept by the matter-close workflow when that exists.
- **The tell that a drop was legitimate: the statement reconciles *better* without the line**, because extraction invented it (a row read twice, a daily-balance entry read as a transaction). If reconciliation gets worse, something real was removed, and the returned statement says so.
- `get_by_statement` / `get_by_account` / `search()` exclude dropped lines **by default**; `include_deleted=True` is for the two callers that need every row — showing someone what they removed, and counting what a cascade is about to take.
- Deleting a statement is `reject_statement` reached from the statement row rather than the exceptions queue. Same operation; the gap was reach, since a statement can look fine on ingest and only later turn out to be a mess.
- `delete_account` cascades and previews first. Its warnings are the `_reason_to_keep` conditions, downgraded to warnings — this is a deliberate act, not an automatic cleanup.

Covered by `tests/test_deletes.py`.

**Rejecting a statement deletes it.** Not a status flip — the statement, its transactions, and (when nothing of value would go with it) the account the bad import created. `review_status = 'rejected'` used to leave all of it filtered out of every view but still present, which is the worst of both worlds: nobody can act on invisible rows, and the empty account sits in the inventory forever. `_reason_to_keep()` spares an account that still has statements, sits in a succession chain, or carries attorney judgment (`ownership`, `property_character`, `purpose`, `notes`) — that is work the import did not do and should not undo. The source PDF stays in Storage: one upload can back several statements, so deleting it on one rejection would take another's source with it. Migration 027 applies the same rule to rows rejected under the old behaviour. Covered by `tests/test_statement_reject.py`.

**A line can be corrected after ingestion, but never quietly.** `correct_transaction()` appends a `MANUAL_CORRECTION` flag per changed field — the field, both values, the staff member's name, the timestamp, and an optional reason — so the original stays recoverable from the record that goes into an exhibit. It also writes an `audit_log` entry (§13). Only the nine fields in `_CORRECTABLE` may be changed; structural columns are not editable, because changing which statement a line belongs to is a re-ingest, not an edit.

**Correcting an `amount` re-reconciles the statement.** That is the point of allowing the edit: an unreconciled statement is usually one misread figure. `_rereconcile()` recomputes the close and *replaces* the stale `UNRECONCILED` flag rather than stacking another one, so a statement corrected into balance stops claiming it is out of balance. It deliberately leaves `review_status` alone — clearing an exception is a decision, not a consequence of arithmetic. Covered by `tests/test_transaction_correction.py`.

Pass `Decimal` and `date` straight to `repo.update()`. The manager's `json_safe` already converts them, and converting to `str` first leaves a string on the model handed back to the caller, which then breaks the next arithmetic that touches it.

**`extraction` records who did the work, not just what was asked.** `_provenance()` writes a `passes` list — one entry per LLM call with its label (`index`, `pages 5-8`), vendor, model, and attempt count — plus `models_used` and a `failed_over` flag. Because each pass carries its page range, any line traces to the model that read it. `attempts > 1` on a pass means the preferred model did not answer, which is how a vendor regression gets noticed after the fact rather than never. Covered by `tests/test_extraction_provenance.py`.

**`source_filename`, `bates_first`, and `bates_last` live in the statement's `extraction` jsonb**, not in columns — they are provenance, never queried on. `_statement_response()` in the router lifts them out, because "which file was this?" is the first question asked about a statement that will not reconcile, and Storage renames every upload to a job id.

### Bates Service (`bates_service.py`)

**A Bates number is a pattern problem, not an extraction problem.** The stamp is one token printed in the same place on every page of a production, and its numeric part advances by one per page — nothing else on a bank statement behaves that way. An account number repeats, a balance fluctuates, a check number jumps around.

So it runs in Python over the page-marked text, never through the model. Exact, free, auditable, and it removes a hallucination surface: asked for "the Bates number" on an unstamped page, a model will produce a plausible one, and a citation to a number that is not on the document is worse than no citation.

- **Strict monotonicity is the hard gate**, not unit steps. A production with pages missing is *precisely* a run whose steps are not all one — gating on unit steps would reject the incomplete productions and accept only the complete ones, exactly backwards. Unit steps are evidence used for ranking and confidence.
- **When a series is detected, the pattern owns `bates_number` outright**, including writing `None` for a page it found no stamp on. The model's answer is discarded, not used as a fallback. With no series detected the model's value is kept but the statement carries `BATES_UNVERIFIED`.
- **The gap scan runs over a statement's page SPAN, not its transaction-bearing pages.** Statements are full of pages with no lines: a checkbook reconciliation worksheet, disclosures, a closing page of running balances. Scanning only pages that carried a transaction made every one of those read as a hole — a First Financial statement with entries on pages 1 and 3 reported its own stamped page 2 as missing from the production. The flag fired exactly when a statement had transactions on two non-adjacent pages, which says nothing about the production. `SUSPECT_SPLIT` still uses transaction pages, deliberately: the narrower range means fewer false positives on a combined statement carrying two accounts.
- **An unstamped page we hold is not a gap.** It is projected onto the run so the number it would carry does not read as missing. That inference only ever *suppresses* a false alarm — the projected number is deliberately never written to `by_page`.
- `BATES_GAP` is `warn` (a hole inside a statement usually means missing lines, which is also the reading behind an unreconciled balance); `BATES_UNSTAMPED` and `BATES_UNVERIFIED` are `info`.
- The `bates_prefix` upload field is an override for a document carrying two competing series. It rides on `jobs.params`, because the user types it at the upload and the worker needs it minutes later in another process.

Covered by `tests/test_bates_service.py` and `tests/test_statement_bates_commit.py` — run them directly with the venv interpreter; there is no runner wired up yet.

### Account Discovery Service (`account_discovery_service.py`)

**A production names the accounts it does not contain.** Money moves between
accounts, and the statement you *do* have prints the number of the one you do
not — `Transfer from XXX4070 to XXX9260`, `INTERNET XFER FROM CHKG 8098386837`.
Matching those against `financial_accounts` leaves the accounts nobody produced.

- **Direction comes from the sign of the amount, never the words "to" and
  "from".** One description carries both, so reading the words means guessing
  which number the sentence is about. Money that left the account went to the
  other one; there is nothing to guess.
- **Identity is the last four.** `86110018909-D` and `XXX8909` are one account;
  deduping on the printed string reports two. It also means an account already
  on the matter is never reported — including the statement's own number, which
  it names constantly, so self-reference needs no special case.
- **Banks disagree about the mask character.** First Financial prints
  `XXX4070`, Chase prints `Chk ...9323` with no space before the digits, others
  use `****1234` or `####5678`. The rule is "two or more mask characters, then
  digits", never a literal `XX` — the dot form shipped unmatched and an account
  referenced by name in a produced statement never reached the list. Two or more
  matters: a single dot is the one in "Acct No." and in every decimal amount,
  and a single hyphen is in every date.
- **Three patterns, and each run of digits is claimed once**, by the most
  specific pattern that reaches it. `Acct No. 86110018909` matches both the
  explicit-account pattern and "a label, then a number"; counting both doubles
  the money the account appears to have moved.
- **The institution is an inference and says so.** A name in the description is
  read off the page; without one, the transfer is assumed to stay inside the
  bank whose statement it was printed on, flagged `institution_inferred`, and
  carries a dagger in the UI. A later mention that names the bank outright
  upgrades an earlier inference.
- `_NOT_AN_INSTITUTION` is stripped from both ends of a label rather than
  matched as a phrase: what sits before an account number is account-type words
  in any combination (`DDA Acct No`, `CHKG`, `SVGS`), and enumerating
  combinations is a losing game. What survives is a name, or nothing.
- **A wire names the sending INSTITUTION, never the sending account**, so it
  cannot join the account list at all — there is no number to key on.
  `referenced_institutions()` is the second half of the report, and it exists
  because the account-shaped scan was structurally blind to $198,101.18 of
  incoming wires on the Harrison matter while catching a $500 intra-bank
  transfer. Keyed on the **ABA routing number**, which is checksummed (3-7-1
  weights, sum ≡ 0 mod 10) and therefore safe where a bank name spelled two ways
  is not.
- **The finding is not "money came from UBS" — it is "she wired it to
  herself".** `B/O:` and `Bnf=` are the Fedwire originator and beneficiary; when
  they are the same person, money moved out of an account that party controls
  and into one we hold. `_same_party` requires **two** shared name words, the
  same rule `intake_service._match_confidence` uses — a surname alone is not
  enough, and both lines are padded with address text that would otherwise match
  on "United States".
- **The routing number must never reach the account list.** Loosening the digit
  patterns to catch wires would report "UBS ····7993" as an undisclosed account,
  which is the confident-wrong failure this whole module is built to avoid.
- Wire vocabulary is matched on the **Fedwire tags** (`B/O:`, `BNF=`, `ORIG:`)
  rather than any bank's phrasing, because the tags come from the message format
  and survive a change of institution. The word "wire" alone would also match
  every VERIZON WIRELESS line — cheap at the query, rejected by the parser.
- **A payment is a transfer with only one side named**, and that is a third
  shape of evidence with its own method (`creditors()`). Measured on one
  production's payment lines: **two** of roughly a dozen credit relationships
  printed an account number; the rest name only a payee. `_ENDING` catches the
  two (Chase spells a mask in English — "Card Ending IN 9547"), and they join
  the account list like any transfer reference.
- **`_LABELLED` is transfer-only, deliberately.** On a transfer,
  `FROM CHKG 8098386837` is a bank convention. On a payment, a trailing digit
  run is a confirmation number essentially always — widening the gate without
  this split reports *"Kathy Gunn ····0159"* as an undisclosed account, the
  number being her Zelle confirmation. A payment must name its account in a
  form that says so: a mask, an "Acct No.", or "ending in".
- **Nothing in a payment description can say whether the payee is a creditor.**
  "Online Payment To Mr. Cooper" (a mortgage servicer) and "Online Payment To
  Frontier" (an ISP) are the same sentence, and no regular expression separates
  them because the difference is not in the text. Two sources answer it, and
  every row says **which**: a category flagged `is_liability` (a paralegal
  filing a line under "Credit Card Payments" IS that assertion, made as
  ordinary work), or a row in `transaction_payee_classifications`.
- **The negative verdict is what makes the report survive a real production.**
  Without a way to record "Atmos Energy is not a creditor" once, forty utilities
  come back on every matter and the list stops being read. `not_creditor` is
  therefore stored, attributed, and reversible — it suppresses evidence, so it
  is never a silent default and is never seeded.
- **Everything unruled is a CANDIDATE, never a finding**, returned in its own
  list, ranked by money, and **excluded from the exhibit entirely**. Putting an
  unclassified payee in a document filed with a court asserts of a water utility
  exactly what it asserts of American Express.
- `_payee_key()` strips what varies between payments of one creditor — dates,
  trace and ACH ids, channel furniture like `CHK CARD PUR` — so twelve payments
  group as one row. It removes bank furniture and never removes words: a payee
  that scrapes into two near-identical groups is cosmetic and collapses the
  moment somebody rules on it, because a ruling's own pattern then becomes the
  key. A scrub aggressive enough to merge two different creditors would not be
  recoverable.
- **A line that scrubs to nothing is dropped without trace**, which is why
  "Venmo Payment" is not stripped as a lead phrase — left alone it groups under
  VENMO, a visible fact somebody can dismiss. A payment rail is furniture but a
  *name* on it is not: "Zelle Payment To Kathy Gunn" keeps the name, because a
  Zelle to a person can be a private loan being repaid.
- The scan runs over **deposit-side accounts only**. A payment arriving on a
  card describes the checking account that funded it, not a creditor. And only a
  produced **credit** account explains a creditor payment — a produced Chase
  checking account says nothing about a Chase card.
- Ruling matching goes through `category_rule_service.matches()`, the same
  boundary-aligned matcher the rules use, rather than a second implementation
  that would silently match TARGET inside STARGETTER LLC.
- **An ACH trace number's first eight digits are a real routing number** —
  `TRACE #-091000014282361` resolves to 091000019, Wells Fargo — and it must
  never reach the institution list. That is the *creditor's* bank: keying on it
  would report an undisclosed account at Wells Fargo because somebody pays their
  Amex bill.
- **A mask needs no verb.** The gate exists so a confirmation number is not read
  as an account — but nobody masks a confirmation number, so `XXXXXXX3640` is
  the bank asserting these are an account's last digits whatever sentence it
  sits in. Requiring a movement word as well meant Capital One's
  `Deposit from 360 Performance Savings XXXXXXX3640` was invisible while the
  identical shape carrying the word "Payment" was not. `_MASKED` and `_ENDING`
  therefore open the gate by themselves.
- Three ways in, and they are not equally trusting: a **transfer** gets every
  pattern including `_LABELLED`; any **other movement** (payment, deposit,
  withdrawal) gets the explicit patterns only, because a trailing digit run
  there is a confirmation number essentially always; a **mask** gets in on its
  own.
- The gate is pushed to the database as one text search per term
  (`transfer`, `xfer`, `payment`, `pmt`, `autopay`, `deposit`, `withdraw`,
  `xxx`), so the scan fetches candidate lines rather than every line on the
  matter. The query terms and the regexes are deliberately the same words —
  including the mask term, or a line the parser would accept is never fetched.

Covered by `tests/test_account_discovery.py` and `tests/test_undisclosed_exhibit.py`.

### Exhibit Service (`exhibit_service.py`)

Turns any query result into a court exhibit. One `Exhibit` describes the
document — caption, selection, table, totals — and four renderers draw it.
Anything in Cyclone that produces a table worth taking to court builds an
`Exhibit`; none of them learn to write DOCX.

- **Every report gets the same four formats.** `components/ExportButtons.tsx` is
  the one control — the caller supplies the request, everything about presenting
  the result (including the caption warnings) lives there. Two copies would
  drift, and the copy that quietly stopped showing warnings is the one nobody
  would notice.
- **`Row` carries hierarchy**: `depth`, `heading`, `rule`. Added for the FIS,
  where the indentation *is* the form — "Airfare" under "Travel" under
  "Entertainment" is what the line means. A bare tuple still works everywhere it
  did, because `Row` iterates, indexes, and measures as its cells, so no flat
  report opts in to a hierarchy it does not have. Normalised in `__post_init__`
  **and** at each renderer, because assigning `exhibit.rows` after construction
  is reasonable and skips the former.
- **`show_headers=False` for a form.** A Financial Information Statement prints
  no column headings; its columns are self-evident. The CSV prints them anyway —
  that file is data, and data without a header row is a puzzle. Markdown still
  emits the separator row, or the block stops being a table.
- **`sources` names the documents the exhibit summarizes**, and sits directly
  above the Rule 1006 notice because the two are one argument: the notice says
  the underlying records are available for examination, and this says *which*
  records, by the filename as produced and the Bates range on it. Grouped by the
  **upload**, not the statement — one PDF routinely holds several statements, and
  a list naming each separately would send somebody to the same document five
  times while looking like five documents. The range shown is the document's own
  extent, not the pages the selected rows happened to land on: a summary drawn
  from three lines inside a statement is still drawn from that statement, and
  the person pulling it needs the whole thing. An unstamped production says "no
  Bates stamp detected" rather than leaving a blank, because a reader cannot
  tell a blank from nobody having looked. Absent from the CSV, like the caption.
- **`footnotes` ride with the table**, not in `selection`. A reader meeting a
  dagger in a cell looks directly below the table for what it means; a mark
  whose explanation stayed on the screen is worse than no mark, because the
  reader can see something is hedged and cannot tell what.
- **CSV is deliberately not an exhibit.** Header row, data rows, nothing else,
  so it opens in a spreadsheet or goes to a model without a preamble to strip.
  The caption and the verification notice live in the other three formats. A
  UTF-8 BOM is written because Excel reads a plain UTF-8 CSV as the system
  codepage and mangles any non-ASCII payee name.
- **The caption is a template, not renderer code.** `_SYSTEM_CAPTION` is a few
  format strings using `**bold**` / `__underline__` and `{placeholders}`.
  Firms disagree about captions, and always about wording and order — never
  about how bold text is written into a .docx. `caption_lines()` already takes
  the template as an argument, so the future per-firm override is a stored list
  of strings and no renderer changes. **FUTURE:** that override belongs in a
  table keyed the way `matter_preferences` will be — NULL user id means the
  firm's, a row means that user's.
- **Parse the markup first, substitute after.** Interpolating before parsing
  lets a *value* be read as markup: the blank rule is a row of underscores and
  gets eaten as an `__underline__` marker, and a case style containing `**`
  corrupts the rest of the caption. A value is content; only the template
  carries style.
- **A missing caption field is a printed blank and a warning, never "None".**
  An attorney wants the numbers long before a cause number exists. Warnings ride
  back on `X-Exhibit-Warnings` so the UI can say what was left blank instead of
  a blank reaching a filing unnoticed.
- Markdown coalesces adjacent runs of the same weight before emitting markers —
  per-run emission closes and reopens emphasis mid-phrase
  (`**Cause No: ****DF-24-01234**`), which renders as literal asterisks.
  Underline has no markdown spelling and is dropped.
- **PDF goes through PyMuPDF's `Story`**, which is already a dependency because
  it reads the statements. WeasyPrint renders richer CSS but needs native Pango
  and Cairo in the image — a real cost for a caption and a ruled table.
- **Story has no page model, so `<thead>` does not repeat.** Handed 120 rows it
  emits four pages and pages two to four begin mid-data, leaving the reader to
  count columns to find the amount. The table is therefore not one story: rows
  are measured a page at a time — try a batch, shrink until `place()` reports it
  fitted, then grow while it still does — and each page gets its own small table
  carrying its own header. The fit is exact, not conservative. Page numbers are
  stamped after layout, because the total is not known until then.
- **MuPDF's built-in fonts substitute ligature glyphs, and this is accepted.**
  The PDF's text layer holds `oﬀered` where the page reads `offered`, so Ctrl-F
  and text extraction miss words containing ff/fi/fl. The page itself is correct.
  `font-variant-ligatures`, `font-feature-settings`, serif and sans were all
  tested and none suppress it; only monospace does, which is wrong for an
  exhibit. Fixing it means vendoring a TTF into the repo. **Tom's call,
  2026-09-01: not worth it — searchability does not matter in a Rule 1006
  exhibit, and the DOCX is fully searchable anyway.** Tests that search PDF text
  normalise ligatures rather than pretending the substitution is not happening.
- Every exhibit carries the Rule 1006 notice, and it names automated extraction
  explicitly. A wrong date inside a statement period reconciles cleanly and
  reaches an exhibit unflagged; that is measured, not hypothetical.

Covered by `tests/test_exhibit_service.py` and `tests/test_transaction_export.py`.

### Account Number Service (`account_number_service.py`)

**The account number is a pattern, not a comprehension task** — the same
argument as Bates. It is the one long digit run printed on *every page* of a
statement; a barcode is regenerated per mailing, a transaction reference is
unique to its line, a check number appears once, a mail-routing line prints on
the first sheet only.

Measured on twelve months of Chase statements from one production, same model
(`claude-opus-5`), no failover on any of the 24 runs:

| Account | Read the number | Returned null |
| ------- | --------------- | ------------- |
| x4448 (checking) | 9 | 3 |
| x5410 (savings) | 3 | 9 |

Chase prints the number in a text object that extracts **fourteen lines above
its own "Account Number:" label**, past a barcode and a mail-routing line, so
the model sees an empty label. Two runs stored the literal string
`"Account Number:"` as the masked value — the model landing on the label and
finding nothing after it. Sometimes it scans up and finds the orphan; sometimes
it does not.

- **A coin flip on the dedup key is worse than a clean failure.** `find_match`
  returns None without a last four, by design, so nine null reads opened nine
  separate accounts for one savings account — one per month, each holding a
  single statement, and invisible as a problem until somebody asks for the
  balance history.
- **`reconcile()` is deliberately not the Bates rule.** There the pattern owns
  the field outright, because a model asked for a stamp on an unstamped page
  invents a plausible one. An account number is also printed in prose ("your
  Chase Savings account ...5410"), so a model answer that *disagrees* with the
  pattern is a real conflict, not a hallucination: keep the extracted value and
  raise `ACCOUNT_NUMBER_CONFLICT` (warn). A null read is filled from the pattern
  (`ACCOUNT_NUMBER_DERIVED`, info); agreement is silent.
- **"Most pages", not "every page".** Every page was the first rule shipped and
  it was wrong: statements open with pages carrying no account number at all —
  a brokerage package leads with a cover sheet and often a letter about a change
  of terms, and disclosures or inserts turn up anywhere. One such page vetoed
  the real answer and the detector went silent on the documents it was built
  for. Length is never the tiebreak either: the barcode is the longer run.
- **`ask()` is the failover when repetition cannot settle it** — the single
  question to a `fast` model over the first three pages
  (`find_account_number`), because the full prompt asks for twenty fields and
  this one gets a line of its attention. **The answer is verified against the
  printed text before it is accepted**, comparing each printed run separately so
  a "match" cannot straddle two unrelated numbers. The model locates a value; it
  never produces one. It is offered only pages known to belong to this statement
  — on a two-statement PDF with no page span, asking would find the other
  account's number and file this statement against it.
- **A statement's transaction pages are not its extent** — the same rule as the
  Bates gap scan, relearned here the hard way. Every Chase transaction prints on
  page 1 and page 2 carries only disclosures, so scoping the search to
  transaction pages showed the detector a single page. One page cannot
  demonstrate repetition, so it declined, and the lookup then saw one page
  holding both the account number and a barcode and chose the barcode on twelve
  statements running. **When the document holds one statement, the document is
  the statement.** Only a multi-statement document is scoped by page, and one
  with no page numbers on its lines is skipped entirely rather than searched
  whole — that would find another statement's number and file this one under it.
- **`ask()` carries the same length bound as `detect()`.** It is the only reason
  `detect` never picked a barcode: a Chase mail barcode is 20 digits where the
  account number is 15. And "printed on the page" is too weak a test on its own,
  because a barcode is printed on the page — so when several pages were
  examined, an answer appearing on one loses to a run appearing on more. An
  account number repeats; a barcode is regenerated per mailing.
- **One page proves nothing about repetition** and returns None — a lone page's
  longest digit run is a guess dressed as a pattern. A document holding two
  statements likewise has no run on every page; detection is scoped to the
  statement's own page span, and falls back to the extraction when that is
  unknown.
- `looks_like_a_number()` rejects a masked form carrying no digits. A caption
  stored as a value makes a wrong answer look like a recorded fact.

Covered by `tests/test_account_number.py`.

### FIS Service (`fis_service.py`)

Averages a person's income and expenses over a window of **whole months**, one
line per category. Three things make it harder than dividing by the month count,
and all three are the same failure: a document that looks complete and
understates.

- **Recurrence is arithmetic, not a label.** A payment made quarterly or
  annually buys coverage past the window: $3,600 of property tax paid once in
  January reads as $1,800/month over Jan-Feb and $1,200 over Jan-Mar. Same facts,
  a different sworn figure every time the report re-runs — *"Counsel, which
  figure did your client swear to?"* Sub-monthly lines are therefore computed
  from the **trailing twelve months over twelve**, chosen over counting
  occurrences because it also sums two tax parcels correctly (occurrence-
  averaging halves them) and still finds a payment made *outside* the window,
  where window-only arithmetic prints a blank line — as wrong as an inflated one.
- **A liability account's sign is inverted, here and only here.** Everywhere
  else an amount is signed by how it moves the balance the institution prints —
  which is what makes `beginning + sum == ending` hold for every account type,
  and which makes a credit card purchase POSITIVE because it raises the balance
  owed. The FIS asks a different question: what did this household earn and
  spend. Under the printed convention those answers are opposites for a card or
  a loan, so $500 of groceries on debit (-500) and $500 on credit (+500)
  **cancelled to nothing**, and a month lived on plastic reported as income.
  `household_amount()` negates `credit_card` and `loan`; the stored value is
  untouched, because reconciliation still needs the printed sign and the
  transaction exhibit still shows what the statement shows. A card PAYMENT
  inverts to positive under this rule, which is right only because the payment
  must be excluded anyway — it is the same money as the withdrawal from
  checking, and that is the double-count `include_in_fis` exists to prevent.
- **The window is whole months, by construction.** There is no way to ask for a
  part-month, because "average monthly" over three-and-a-bit months cannot be
  explained on the stand.
- **The denominator is a claim about coverage.** Dividing by eight months asserts
  eight months of statements. `_coverage` checks that per account and reports the
  missing ones by name; the warning travels onto the exhibit, not just the screen.
- **Uncategorized money gets its own row** with a count, outside the form. A line
  nobody filed appears in no category while the net still looks authoritative.
- **`include_in_fis=False` categories are excluded but listed**, so an
  interaccount transfer reads as set aside rather than lost.
- **The net sums the ROUNDED lines**, so the printed column adds up to the total
  beneath it. A net that differs from the visible column by three cents is a
  question nobody wants asked on the stand.
- **Compression is computed server-side** (`_mark_empty`), so the screen and the
  exhibit cannot disagree about which lines a condensed statement shows. A
  heading survives on its own figure *or* a surviving descendant — a transaction
  can be filed straight to a heading, and dropping it would drop the money.
- The scan is deliberately **uncapped**, unlike an export: an exhibit that stops
  at five thousand rows says so on its face; an FIS that stopped would silently
  understate every line.

Payment schedules live in `fis_category_settings` and are **scoped to the person,
not the matter** — a schedule is a fact about someone's finances, and the same
client may have matters in several counties from successive marriages. Two
layers, as with tags: no party set is the firm-wide default, a party row
overrides it. `stated_annual_amount` is the attorney's own figure and wins over
anything derived, signed like every other amount here because the reason to
state it is that the transactions show none.

Covered by `tests/test_fis_service.py` and `tests/test_fis_exhibit.py`.

### Category Rule Service (`category_rule_service.py`)

Files transactions by keyword at ingest, so a paralegal meets the ambiguous
lines rather than the thousandth grocery run. Three rules make an automatic
assignment defensible rather than merely convenient:

- **A rule never overwrites a person.** It fills a line nobody categorized, or
  replaces one the machine itself set. A category with `category_source` NULL
  but a `category_id` is treated as human work — it almost certainly is,
  predating the provenance columns, and guessing wrong in that direction
  destroys real judgment.
- **Every assignment records `category_source` and `category_rule_id`.** That is
  what makes the review queue possible, a bad rule reversible in one query, and
  *"why is this Household Supplies?"* answerable in one sentence.
  `category_rule_id` is deliberately **not** a foreign key: the trail has to
  outlive the rule, and it is most wanted right after somebody deletes the rule
  that caused the problem they are investigating.
- **Matching is punctuation- and case-blind**, both sides flattened to
  uppercase alphanumerics, so `WALMART` finds `WAL-MART #1234`,
  `WAL MART SUPERCENTER` and `WALMART.COM`. It is a **substring** match, so
  `ATM WITHDRAWAL` finds `IN PERSON ATM WITHDRAWAL AT 5700 W PLANO PKY`.
- **But flattening erases the word boundaries, so they are kept and enforced.**
  A plain substring test on the flattened text matches `TARGET` inside
  `STARGETTER LLC`, `ROSS` inside `CROSSROADS MARKET`, and `MARTS` inside
  `WAL MART SUPERCENTER` — each a confident, silent mis-filing of evidence.
  `prepare()` returns the offsets where words begin and end, and a match must
  land on them at both ends. Every occurrence is tested, not just the first:
  `MART` sits inside `SMART` at one offset and starts a real word at another.
- Description and counterparty are searched **separately**, never concatenated,
  so a match cannot straddle the two and invent a merchant nobody paid.

Ordering is `(priority, -len(pattern), id)`. The length tiebreak is load-bearing:
two rules at equal priority where one pattern contains the other — `WALMART` and
`WALMART PHARMACY` — must try the longer first or the general rule wins every
time and the specific one may as well not exist. `applies_to` constrains a rule
by sign, because PAYROLL arriving is income and PAYROLL leaving is an expense.

**This does not weaken the rule that extraction never sets `category_id`.** What
that forbids is the *model's guess*, which is unexplainable and varies run to
run. A keyword rule is firm-authored, deterministic, and answerable on the
stand. The provenance is what keeps the distinction visible.

**A failure to load rules never fails an ingest.** The statement is the evidence;
filing it is a convenience laid on top, and the table may not exist yet on a node
where 032 has not been applied. Logged at WARNING, because rules that quietly
stopped firing would look exactly like rules nobody wrote.

Covered by `tests/test_category_rules.py`.

### Transaction Search Service (`transaction_search_service.py`)

Everything downstream of ingestion. Two classification axes, deliberately different mechanisms:

- **`display_order` orders SIBLINGS, not the whole tree.** It was documented as
  a key across the tree and that cannot express nesting: in the deployed chart
  Utilities and Lawn/Landscaping both sit at 115 while Utilities' children start
  at 120, so a flat sort put Electricity and Telephone *after* Pool and Other
  Staff — indented under a branch they do not belong to, which reads as no
  hierarchy at all on the FIS and in every picker. `get_ordered()` walks the
  tree depth-first, siblings by `(display_order, id)`. Use it, not `get_all()`,
  anywhere a person will read the result. A child whose parent is filtered out
  is appended rather than dropped: it may still have transactions filed to it.
- **Category** — one per transaction, from the firm-wide `transaction_categories` hierarchy. Drives the Financial Information Statement, where a line in two buckets double-counts. `include_in_fis` is what keeps a stock split off an income statement. Extraction never sets `category_id`; the free-text `category` column is the model's guess and is only ever a hint.
- **Tags** — many-to-many, two layers in one table (`matter_id` NULL is firm-wide). Drives the Rule 1006 summaries. One line is routinely evidence in several exhibits at once.

**A matter's accounts are the search scope.** Transactions carry no `matter_id`, so every query resolves the matter's accounts first and intersects any account filter against them — that is what stops a crafted request reaching another matter's records. `_verify_on_matter()` does the same for every mutation.

**An export is not a page.** `build_exhibit()` pages through every matching
line, because the screen shows 200 rows and an exhibit that stopped there would
be a summary of the wrong set — and would look complete. Hitting `_EXPORT_CAP`
is reported both in `warnings` and in the exhibit's own Selection block, where
a reader of the finished document sees it.

**`selection` is what makes an exhibit usable by someone who did not run the
query.** A table of forty transactions means nothing without the criteria that
produced it — not to a judge, not to opposing counsel, and not to a model asked
to lay it out. Filters that were not applied are omitted rather than listed as
"none", so the block reads as a description and not as a form. `include_deleted`
is always stated when on.

`FinancialAccountTransactionRepository.search()` is the one place that builds a PostgREST query by hand instead of passing a condition dict: the dict supports equality, `IN`, and null checks only, so a date window and an `ILIKE` cannot be expressed through it at all. The "untagged" filter is expressed as *exclude the tagged ids*, never as the complement — the complement is the whole production and would blow both the URI limit and PostgREST's max-rows cap.

### Audit Logger (`audit_logger.py`)

`AuditLogger.log()` **never re-raises on failure** — audit logging must not crash the primary operation.

### Key LLM Use Cases

| Feature | Endpoint | Notes |
| --------- | ---------- | ------- |
| Natural language billing entry | `POST /api/v1/billing/parse` | `parse_billing_entry` — preview with resolved rate + amount; not committed until attorney confirms |
| Discovery request ingestion | `POST /api/v1/discovery/upload` | `classify_discovery_document`, then `extract_discovery_items` |
| Pleading ingestion — metadata | `POST /api/v1/pleadings/preview` | `analyze_pleading` — case metadata, children, opposing counsel |
| Pleading ingestion — claims | (same call) | `extract_pleading_claims` — second LLM call inside preview_ingest |
| PDF vision OCR | (internal to pdf_service) | `ocr_document_page` — for scanned pages with no text layer |
| CRM lead agent | (internal to crm_agent_service) | `compose_welcome_email`, `triage_lead_message`, `extract_lead_issues`, `select_kb_articles`, `compose_lead_reply`, `response_guardrail`, `explain_message_edit` |

---

## 11a. Background Jobs (`job_service.py`, `crm_worker.py`, `jobs` table)

**Anything that can take more than a few seconds must not hold an HTTP request open.** Production sits behind haproxy, whose `timeout server` cuts the connection long before nginx's 300s. The client gets a 504 with *no error in the API log*, because nothing failed — the request was still working. Reading a pleading is one LLM vision call per image-only page plus two more over the text; on a scanned document that is minutes.

The pattern:

1. **Upload** stores the input and inserts a `jobs` row, returning **202** with a job id in well under a second.
2. **The worker** claims the row, does the work, and writes `result` (jsonb) or `error`.
3. **The caller polls** `GET .../jobs/{id}` every couple of seconds until the status is terminal.

Rules learned the hard way:

- **Store the input before the row is queued.** `enqueue_matter_intake` generates the id in Python, uploads the PDF, and *then* inserts with `storage_path` already set. Inserting first publishes a queued job whose input is still uploading, and a worker polling in that window claims it and fails with "Job has no stored PDF".
- **Claiming is two-layered.** A Redis `SET NX` (`claim_once`) stops two nodes starting the same job in the same instant; the `queued → running` transition holds it after the key expires. Redis being down degrades to the status check rather than halting the queue.
- **Job claiming is deliberately outside the `crm:poller` lock.** That lock makes mailbox polling fleet-wide single-runner; jobs are claimed individually, so every node should take work.
- **Failures land on the job,** with the reason in `error`. A failed upload must be able to tell the attorney what happened.
- **Polling is scoped to the requester** (`get_for_staff`) — a result holds the full text of someone's pleading.
- The worker runs **two cadences**: jobs every `job_poll_interval_seconds` (short — someone is watching a spinner), CRM polls on their own much slower schedule.
- **Jobs run in a pool of `job_concurrency` (default 5), and the loop never waits on one.** It used to run each job to completion inline, so a single statement — a thirteen-month upload still going at 1,600 seconds — stopped everything: no other job started, and the CRM tick did not run either, which is why a long ingest was indistinguishable from a dead worker. `claim_pending` takes only what there is free capacity for and `run_claimed` runs it on a pool thread. A worker at capacity claims **nothing**, leaving the queue for a node with room rather than building a private backlog.
- **A manager per job, never per worker.** `SupabaseManager` is not thread-safe (`dependencies.get_db_manager` says so); one shared across pool threads interleaves two ingests' requests down a single connection.
- **The upload side has to match, or the pool is pointless.** The page uploads the whole dropped stack before waiting on any of it (`queueStatement`, then `awaitStatementJob` for each). Uploading one and awaiting it before sending the next left the pool with exactly one job however many files were dropped — which is what made merging a year into a single enormous PDF look like the only way to finish.
- Concurrency trades against **vendor rate limits**: a 429 fails the candidate over to the next model in the chain rather than waiting, so too much of it quietly changes which model read the evidence. Lower `job_concurrency` before raising it.

Pleading ingestion and discovery upload still run extraction inline and carry the same exposure. The `jobs.kind` CHECK constraint is where they get added.

---

## 12. Business Logic: Billing

### Rate Resolution Order (BillingService.resolve_rate)

1. **Pro bono short-circuit:** if `matter.is_pro_bono` is True → rate=0, amount=0 (returns immediately)
2. `matter_rate_overrides` — per-staff, per-matter override row
3. `matter.rate_card` — typed `RateCard` Pydantic model with `attorney` and `paralegal` optional float fields
4. `staff.default_billing_rate` — staff member's default rate

Enforced in three places:

- Python: `BillingService.resolve_rate()` (primary)
- SQL function: `resolve_billing_rate()` (for reporting queries)
- DB trigger: `enforce_pro_bono_zero_rate` (backstop on INSERT/UPDATE)

### Billing Entry Creation

- `staff_id` is resolved from the authenticated user's JWT if not explicitly provided
- `entry_date` is always set server-side to today (the date the entry was recorded)
- `invoice_date` is the date work was performed — defaults to today, can be parsed from NL input by the LLM ("last Friday", "on April 3"), or explicitly picked via date input in the UI

### Immutability Rules

- Billed entries cannot be edited — `prevent_billed_entry_edit` trigger
- Trust ledger entries cannot be updated or deleted — `deny_trust_ledger_mutation` trigger
- Audit log entries cannot be updated or deleted — `deny_audit_log_mutation` trigger

---

## 13. Audit Log

Sensitive actions must write a record to the `audit_log` table **in addition to** the application log. Use the `AuditLogger` service — do not write to `audit_log` directly from route handlers.

Actions requiring an audit log entry:

- Billing entry created / edited / deleted
- Billing cycle closed
- Bill sent to client
- Fee agreement signed
- Trust ledger transaction posted
- User role changed / correlated

---

## 14. Frontend Conventions

### API Calls

All backend calls go through `src/lib/api.ts`. Never call `fetch()` or `axios` directly from a component — except for multipart file uploads, which must set the `Content-Type` boundary themselves and call `fetch` directly while still injecting the Bearer token (see `uploadDiscoveryPDF`, `previewPleading`).

```typescript
import { apiFetch, getMatters } from '../lib/api'
import type { Matter } from '../types'

const data = await apiFetch<MyType>('/api/v1/some-endpoint')
const matters = await getMatters()  // returns Promise<Matter[]> — no cast needed
```

`apiFetch<T>()` automatically attaches the Supabase Bearer token from the current session.

**A thrown error's `message` is the sentence the server wrote, not its JSON.**
`errorMessage()` pulls `detail` out of the body — the string an `HTTPException`
carries, or the joined `msg` fields of a 422 — and every fetch path in `api.ts`
goes through it, including the six multipart uploads that call `fetch`
directly. It used to throw the raw body, so roughly ninety `e.message` display
sites all showed `{"detail":"..."}` with the prose walled up inside it. A body
that will not parse (an haproxy 504 answers in HTML) falls back to the status,
because rendering markup into an error dialog is worse than saying "504".

### Shared Types

TypeScript types mirror backend Pydantic schemas and live in `frontend/src/types/`. Each domain has its own file (client, matter, staff, billing, discovery, pleading, etc.), all re-exported from `types/index.ts`. **Never redefine a type inside a page component** — import it from `types`.

API functions in `api.ts` return typed promises (`Promise<Matter[]>`, not `Promise<unknown[]>`). Call sites don't need `as Type[]` casts.

### Supabase JS Client

Used **only** for auth session management (`src/lib/supabaseClient.ts`). Do not use `supabase.from(...)` for data queries from the frontend.

### Routing

- Public routes: `/`, `/login`, `/auth/callback`, `/onboarding`, `/access-denied`, `/privacy`, `/terms`
- Protected routes: `/app/*` wrapped in `ProtectedRoute` → `AppShell`
- `ProtectedRoute` redirects to `/login` (no session), `/onboarding` (no role), or `/access-denied` (client role — client portal not yet built)
- Admin-only nav items filtered by role in `AppShell`

### Dual-Density Layout

`AuthContext` sets `document.body.dataset.density` based on the user's role:

- `data-density="relaxed"` — client portal (generous spacing, larger type)
- `data-density="compact"` — staff portal (tighter grid, more info per viewport)

### Styling

- Tailwind CSS utility classes only — no inline styles
- Custom component classes in `src/index.css` `@layer components`: `btn-primary`, `btn-secondary`, `btn-gold`, `card`, `input`, `label`
- Custom colors in `tailwind.config.js`: `navy`, `gold`, `off-white`, `success`, `warning`, `danger`, `text-primary`, `text-secondary`, `border`
- Fonts: `font-display` (Playfair Display), `font-sans` (Inter), `font-mono` (JetBrains Mono)

---

## 15. Docker & Deployment

### Running with Docker

```bash
docker compose up -d                          # Dev (override auto-applied): hot reload, DEBUG logging, ports 3000/8000
docker compose -f docker-compose.yml up -d    # Production: frontend on :8094, API internal only
```

### Production config

- Images are tagged: `ghcr.io/tjdaley/jdbot-cyclone-{api,frontend}:X.Y.Z`
- **The `worker` service runs the same image as the API.** Backend changes need *both* rebuilt — shipping only the API leaves jobs queued and nothing running them.
- Frontend exposed on host port `8094` (behind haproxy)
- API has `expose: "8000"` — internal to Docker network only; nginx proxies `/api/*` to it
- `.env` file is both `env_file`-injected AND mounted at `/app/.env:ro` so Pydantic can also read it
- Healthchecks: API uses `python urllib` against `/api/health`; frontend uses `wget --spider` against `http://127.0.0.1:80/` (must use IPv4 literal, not `localhost`, due to Alpine's IPv6-first resolution)
- nginx has `proxy_read_timeout 300s` for API calls and `client_max_body_size 50m` for large PDF uploads
- **haproxy sits in front of nginx and is the real request-duration limit.** Its `timeout server` is shorter than nginx's 300s, so a slow endpoint returns haproxy's own 504 page ("The server didn't respond in time") with nothing in the API log — the request was still running. This is why long work goes through the job queue (§11a). Recognizing whose 504 you have matters: nginx stamps `nginx/<version>` in its error page, haproxy does not.
- **Migrations are run by hand, in numeric order.** `run_all.sql` only covers `001–005`. When a change needs DDL, name the migration file explicitly so it can be run before the deploy.

### Running without Docker

```bash
# Terminal 1 — backend
uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

Vite dev server (port 3000) proxies `/api` to `http://localhost:8000`.

### `.gitignore` — one trap, already sprung

The Python template's `lib/` and `lib64/` are **unanchored**, so they matched
`frontend/src/lib/` as readily as a root build directory. `api.ts`,
`supabaseClient.ts`, `money.ts`, and `categories.ts` — the whole API layer —
were therefore never committed, and a clean clone could not build the frontend.
Both rules are now `/lib/` and `/lib64/`. When adding an ignore rule copied from
a language template, anchor it, or it will match a source directory three levels
down that shares a common name.

### Startup Checks

1. Pydantic validates all `Settings` fields — raises `ValidationError` before accepting requests
2. `SupabaseManager.__init__` raises `ValueError` if `supabase_url` or `supabase_service_role_key` are empty
3. `AuthMiddleware` fetches JWKS from `{supabase_url}/auth/v1/.well-known/jwks.json` at module load
4. Logs at `INFO`: `"Cyclone API started | env=%s log_level=%s llm_profiles: %s"`, then logs a `WARNING` per problem found by `llm_service.validate_profiles()`
5. `util/schema_check.check_schema()` diffs the live columns against every model and logs an **`ERROR`** per mismatch

### Schema drift (`util/schema_check.py`)

A model field with no matching column is not a risk, it is a guaranteed 500: `model_dump()` includes a field even when it is `None`, and PostgREST rejects the whole row for an unknown column (`PGRST204`). That is how `clients.referred_to_staff_id` stayed broken until an intake commit hit it in production.

- Live columns come from PostgREST's OpenAPI document (`GET /rest/v1/` with `Accept: application/openapi+json`) — it describes empty tables too, so no query access and no per-table round trips.
- The table→model map comes from the **repositories**, the only authoritative source: each hands its table name and model class to `BaseRepository.__init__`.
- One HTTP call, ~0.8s, read-only, and every failure is swallowed — a check that cannot run must never stop the API from starting. An unconfigured Supabase logs that it *skipped*, because a silent skip is indistinguishable from "checked and clean".
- **It compares columns only.** CHECK constraints, types, nullability, and foreign keys are invisible to it — a migration that only widens a CHECK (like 016) cannot be verified this way.

**`db/migrations/002_tables.sql` no longer describes the deployed `clients` table.** Production grew six columns that no migration creates; `020` reconciles them. Assume other tables may have drifted the same way and check rather than trust the DDL files.

---

## 16. Implementation Status — Where Things Stand

| Feature | Status |
| --------- | -------- |
| Matter CRUD with rate overrides | ✅ Built |
| Client CRUD with conflict check (Phase 1) | ✅ Built |
| Natural-language billing entry | ✅ Built (with rate resolution and `invoice_date` parsing) |
| Manual billing form | ✅ Built |
| Discovery document ingestion | ✅ Built (PDF upload with LLM vision fallback) |
| Discovery item editing (privileges, objections, interpretations, response) | ✅ Built |
| Discovery response export to Word | ✅ Built |
| Pleading ingestion with preview/commit review | ✅ Built (parties, children, opposing counsel, claims) |
| Pleading lifecycle status + stored PDF viewing | ✅ Built (live/superseded/withdrawn/inactive; signed-URL viewer) |
| Discovery `instructions_to_client` (internal work product) | ✅ Built — never exported |
| Matter detail page (`/app/matters/:id`) | ✅ Built — parties, staff w/ origination meter, children, counsel, claims by pleading |
| CRUD for matter children / claims / counsel links / staff | ✅ Built (API + matter detail page) |
| Matter intake from a dropped pleading | ✅ Built — neutral extraction, "who do we represent", client/lead matching |
| Lead → client + matter promotion, and linking an existing client | ✅ Built (`converted_to_client_id` / `converted_to_matter_id` now written) |
| Background job queue (`jobs` table + worker) | ✅ Built — matter intake and statement ingest; see §11a |
| Account statement ingestion (bank/brokerage/card) | ✅ Built — queued, reconciles itself, exceptions queue |
| Transaction categories (FIS chart of accounts) | ✅ Built — firm-wide hierarchy, seeded by 024 |
| Transaction tags (Rule 1006 exhibits) | ✅ Built — firm-wide + per-matter layers, bulk apply, create/edit/retire/delete UI |
| Transaction search (account/date/category/tag/text) | ✅ Built — `POST /matters/{id}/transactions/search` |
| Undisclosed accounts (referenced but never produced) | ✅ Built — transfer references matched against the matter's accounts, plus institutions named by wires, plus creditors named by payments |
| Creditor discovery from payments | ✅ Built — `is_liability` on the category, `transaction_payee_classifications` for the rest, and a triage queue that never reaches an exhibit |
| Re-import a document (Retry) | ✅ Built — discards every statement the upload produced, then re-reads the PDF. Verifies the source is retrievable **before** deleting anything |
| Combined statements (several accounts, no page break) | ✅ Built — the document is cut at each account's printed header, not by page |
| Open a statement's source PDF | ✅ Built — signed URL as JSON, opened at the statement's first transaction page; says which of the three failures happened |
| Payee-classification management screen | ❌ Not started — the endpoints exist and the triage queue writes through them, but there is no screen to review or reverse a firm-wide ruling |
| Card funded from an unproduced account | ❌ Not started — a "Payment Thank You" on a produced card with no matching debit on any produced deposit account means the funding account was not produced. Same finding, opposite direction, and the match is exact on date and amount |
| Exhibit caption (system-wide template) | ✅ Built — `matters.case_style` + `client_alignment`; per-firm override not built |
| "Documents summarized in this exhibit" block | ✅ Built for the transaction export — filename and Bates range per upload, above the Rule 1006 notice. Not yet on the FIS schedule or the undisclosed-accounts exhibit, which summarize documents too |
| Export query results: CSV / MD / DOCX / PDF | ✅ Built — CSV is a clean extraction; the rest are full exhibits |
| Financial Information Statement generation | ✅ Built — whole-month averaging, recurrence-aware, three-panel UI, four-format export. Chart of accounts still to be populated |
| Long-statement extraction (multi-pass) | ✅ Built — indexed, then walked in page chunks |
| Extraction completeness check | ✅ Built — extracted counts and totals vs the statement's own |
| Bates detection + gap reporting | ✅ Built — pattern-based, per-page, with production-gap flags |
| Account-number detection by pattern | ✅ Built — the digit run on every page; fills a null read, flags a conflict |
| Repairing accounts split by a null account number | ❌ Not started — derive from stored `raw_text`, then merge into the real account |
| Account merge (two rows, one real account) | ✅ Built — previewed, with blocking and forceable conflicts |
| Transaction correction with audit trail | ✅ Built — per-field MANUAL_CORRECTION flags; amounts re-reconcile |
| Reject a statement (discards it and its lines) | ✅ Built — deletes; removes an emptied, uncharacterized account too |
| Delete a transaction / statement / account | ✅ Built — soft for a line, hard for a statement or account |
| Reassign an account to another matter | ❌ Not started — needs `ON UPDATE CASCADE` on the statements→accounts FK, as 026 did for transactions |
| Matter-close workflow | ❌ Not started — must purge soft-deleted transactions |
| Joint / sole account ownership | ✅ Built — `ownership` enum; drives division, so it is never inferred |
| Rule 1006 exhibit export | ✅ Built for transaction queries — every exhibit carries the verification notice |
| Compliance matrix (statements held, by year and month) | ❌ Not started — needs a `matter_preferences` table for the look-back year. Ends with the referenced-but-not-produced list; it is the exhibit behind a motion to compel |
| Export on the undisclosed-accounts report | ✅ Built — its own account-shaped columns; the dagger travels with its footnote |
| OCR fallback for an account number | ❌ Not started — only for a SINGLE-PAGE statement, where repetition cannot work by construction. `detect()` returns None there by design. On multi-page forms the pattern already carries it: measured 9 of 9 on Chase savings statements where the extraction read the number 0 of 9 times |
| Large-transaction query (dollar threshold) | ❌ Not started |
| Inventory &amp; Appraisement | ❌ Not started — ask Tom for the firm's I&amp;A form first |
| Schema drift check at startup | ✅ Built (`util/schema_check.py`) |
| Standard privileges/objections lookup tables | ✅ Seeded |
| Privacy policy + terms of use pages (for Google OAuth cert) | ✅ Built |
| Conflict checking | ⏳ Done manually **outside** Cyclone today. Planned home: at the `leads` table, so every promoted client has passed it. `conflict_check_run` already exists in the lead action enum |
| Phase 2 conflict checking (pg_trgm) | ⏳ SQL ready, Python wiring uses substring match only |
| Re-extract / delete a pleading | ❌ Not started — `raw_text` is stored, so re-extraction needs no re-upload |
| Moving the intake PDF onto the matter at commit | ❌ Not started — lands at `intake/{job_id}.pdf`; `StorageService.move()` exists |
| Client Portal (separate from staff) | ❌ Not started — `client` role exists but redirects to `/access-denied`. Intended functions are listed below |
| — Client re-categorizes their own transactions | ❌ Not started. **Depends on auditing category changes, which does not exist yet** (see below). Staff then review "changes since <date>" — the point is not convenience, it is catching a client who spreads gambling across a dozen categories or parks a disclosable transfer under an off-FIS bucket like Interaccount Transfers |
| Audit trail for category changes | ⏳ **Half built.** 032 adds `category_source`, `category_rule_id`, and set/reviewed by-whom-and-when, and the rule engine writes them. `set_category` (the manual path) still writes nothing — that is the remaining half, and the one the client portal needs. Was: `set_category` writes `category_id` and nothing else — no flag, no `audit_log` row, no who/when/from/to. `correct_transaction` does all three for a field edit, and a category change is the bigger act: it moves money between lines of a sworn document. Needed before any client touches classification, and worth having for staff regardless |
| Detail schedule behind the FIS (by category, with provenance) | ✅ Built — backend and export; UI not yet |
| Auto-categorize by keyword rule | ✅ Built — at ingest and re-runnable; never overwrites a person |
| Auto-categorize by similarity to a curated library | ❌ Not started — pg_trgm via RPC. Must record the matched exemplar, or the classification is unexplainable on the stand |
| PDF bill generation (WeasyPrint) | ❌ Not started |
| Stripe checkout / webhooks | ❌ Keys configured, no handler |
| Email notifications | ❌ Not started |
| Fee agreement templates + e-sign | ❌ Model exists; no UI or workflow |
| Client intake form / StepWizard | ❌ Not started |
| Shared components (DataTable, ConfirmDialog) | ❌ Pages use inline tables |
| Test suite | ❌ No unit or integration tests |


---

## 16a. Backlog — asked for, not yet built

Kept here rather than in the status table because these are small, specific, and
were asked for in passing while something else was being built. Roughly in the
order they came up.

### The FIS detail panel

| # | Item |
| - | ---- |
| 1 | **Select-all checkbox** on the transaction list. Today every line is ticked individually. |
| 2 | **Match the detail pane's scroll height to the left pane.** The statement scrolls with the window; the transaction list scrolls independently, which is right, but it is much shorter. Sizing it to the viewport would fix it. Tom: "if not, it's not that big of a deal." |
| 3 | **"Expanded view" checkbox** in the transaction list header, showing account, Bates number and source filename beneath each line in grey. The data is already returned by the schedule endpoint. |

### Reaching the source document — everywhere a transaction or statement is listed

| # | Item |
| - | ---- |
| 4 | ✅ **Done** — `components/StatementPdfButton.tsx` on the exceptions queue and on every statement row in the Accounts tab. Still to do: the same button on a **transaction** row, which needs the transaction's statement id in the search response. |
| 5 | ✅ **Done** — the endpoint distinguishes "never stored", "purged", and "storage is down", and the button shows that sentence in a dialog. |
| 6 | **A retention policy for stored PDFs**, deliberately less generous than the eternal retention of transaction data — a statement's numbers stay long after the scan needs to. Plus a user-requested purge for data hygiene. Interacts with the matter-close workflow, which is also unbuilt. The "purged" message in item 5 is written for a world where this exists. |

### Tell it what to do, rather than learn another menu

Make every UI function addressable as a **tool**, put a chat box at the foot of
the app, and let an agent dispatch: *"Show an FIS in the Miller matter"*, *"Add
Quic Trip to the auto-assignment list pointed at Transportation > Fuel."* Voice
follows from the same plumbing, and demonstrates well. The real argument is that
once you know what a system can do, saying it should be enough — a menu is a
lookup table you have to memorize first.

Most of the work is already done: every function here is a versioned endpoint
with a Pydantic schema, and `llm_profiles.json` is the established way to name a
task without naming a model. What is missing is a tool catalogue and a
dispatcher.

Three things to get right when it is built:

| | |
| - | - |
| **Disambiguation asks, it never guesses.** | "Miller" may be three matters. Picking one and acting is the same class of confident-wrong this codebase spends its effort preventing — a wrong account number, a wrong institution, a rule that files the wrong merchant. A follow-up question costs a second. |
| **Reads execute; writes confirm first.** | "Show me the FIS" can just happen. "Add Quic Trip to Fuel" is a **firm-wide** rule change touching every matter and every FIS built afterwards, and it should be shown before it is done. The `preview_merge` / `merge` split is the pattern already in the codebase. |
| **The transcript is a record.** | If an agent re-files transactions or writes a rule, the existing provenance has to name it as the actor — not the staff member who spoke — or the audit trail says a person did something a machine did. `category_source` would need a fourth value. |

### Larger, already noted elsewhere

- Client portal, and the client re-categorization workflow that depends on
  auditing category changes (§16).
- `set_category` still writes no audit trail — the manual half of what 032
  started (§16).
- Similarity-based categorization against a curated library, once keyword rules
  and review have produced one to curate from (§11).
- OCR fallback for an account number on a single-page statement (§16).
- Compliance matrix, and the `matter_preferences` table it needs (§16).
- Large-transaction query with a dollar threshold (§16).

---

## 17. Environment Variables

| Variable | Used By | Notes |
| ---------- | --------- | ------- |
| `FIRM_NAME` | Backend | Displayed in `/api/config` |
| `ID` | Backend | Deployment identity — which server instance answered |
| `IS_DEVELOPMENT` | Backend | Gates debug behavior and docs |
| `HOST_URL` | Backend | CORS allowed origin |
| `SUPABASE_URL` | Backend + Frontend | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend only | Never expose to frontend |
| `SUPABASE_ANON_KEY` | Frontend only | Used by Supabase JS client |
| `SUPABASE_JWT_SECRET` | — | **Currently unused** — middleware uses JWKS/ES256 |
| `LLM_PROFILES_FILE` | Backend | Task-profile catalog path (default `config/llm_profiles.json`) |
| `LLM_TEMPERATURE`, `LLM_TOP_P`, `LLM_MAX_TOKENS` | Backend | Global sampling defaults |
| `LLM_TIMEOUT_SECONDS` | Backend | Per-call ceiling (default 90). Keep ≥10 — Gemini rejects shorter deadlines |
| `JOB_POLL_INTERVAL_SECONDS` | Worker | How often queued jobs are picked up (default 3) |
| `JOB_CONCURRENCY` | Worker | Jobs one worker runs at once (default 5). Throughput for a stack of statements; trades against vendor rate limits |
| `LEAD_POLL_INTERVAL_SECONDS` | Worker | CRM mailbox/lead polling cadence (default 60) |
| `REDIS_URL` | Worker | Poller lock + job claim; job claiming degrades gracefully if unreachable |
| `{VENDOR}_API_KEY`, `{VENDOR}_BASE_URL` | Backend | Per-vendor credentials only |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Backend | Never expose to frontend |
| `STRIPE_PUBLISHABLE_KEY` | Backend → `/api/config` → Frontend | |
| `LOG_LEVEL` | Backend | Default: WARNING |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL` | Frontend build | Baked in at build time via Docker `args` |

`.env.example` must be kept current. Every new env var must appear in `.env.example` in the same commit.

---

## 18. What NOT to Do

| Don't | Do Instead |
| ------- | ----------- |
| Use `from app.xxx import` in backend code | Use relative imports: `from util.settings import settings` |
| Call `logging.getLogger()` directly | `LoggerFactory.create_logger(__name__)` |
| Instantiate `Settings()` in app code | `from util.settings import settings` |
| Call `supabase.create_client()` outside `supabasemanager.py` | Use a repository class |
| Call `supabase.storage` outside `storage_service.py` | Use `StorageService` |
| Pass a JSON string to `repo.insert()` | Pass `.model_dump()` (a dict) |
| Write business logic in route handlers | Write it in a service class |
| Query Supabase tables from React components | Go through `src/lib/api.ts` → FastAPI |
| Redefine types in page components | Import from `frontend/src/types/` |
| Cast `as Matter[]` on api.ts call results | Use the typed return signatures in `api.ts` |
| Use `orm_mode = True` | Use `ConfigDict(from_attributes=True)` |
| Log PII (names, amounts, case facts) | Log entity IDs only |
| Import LLM SDKs outside `llm_service.py` | Call `llm_service.complete(...)` |
| Add `{VENDOR}_MODEL` / `{VENDOR}_FAST_MODEL` env keys | Add the model to a chain in `app/config/llm_profiles.json` |
| Read a vendor id or model name outside `llm_service.py` | Name the task: `complete(..., profile="analyze_pleading")` |
| Call `complete()` with no `profile` for real work | Name the task; bare `complete()` means the generic `default` chain |
| Import `fitz` / `PIL` outside `pdf_service.py` | Call `pdf_service.extract_text(...)` |
| Hardcode environment-specific values | Use `settings.*` |
| Use `supabase.from(...)` in frontend components | Use typed wrappers from `src/lib/api.ts` |
| Parse an API money string with `Number()` / `parseFloat` | Format the string directly. It is a string so exact cents survive Postgres `numeric`; parsing undoes that where it matters most |
| Store a money value as `float` | `numeric(14,2)` in SQL, `Decimal` in Python, converted via `str` — never `Decimal(some_float)` |
| Decide a transaction amount sign by account type | Sign by effect on the printed balance, so `beginning + sum == ending` holds for every account type |
| Invent a row to make a statement reconcile | Record it unreconciled with the delta; a synthetic balancing row is what gets cross-examined |
| Add a second category to a transaction | Category is one per line — it drives the FIS. Use a tag; that is what tags are for |
| Set `category_id` from extraction | The LLM fills free-text `category` as a hint only; a human files the line |
| Query transactions without resolving the matter's accounts first | Transactions carry no `matter_id`; the account list is the matter boundary |
| Express "untagged" as the complement of the tagged set | Exclude the tagged ids — the complement is the whole production and blows the URI and max-rows limits |
| Rely on `find_period` to catch a duplicated statement | It is account-scoped, so it misses the case where the account itself was misread. Page overlap is the document-scoped check |
| Take a vision answer for the institution at face value | The form printer'"'"'s imprint is the other company name on the page. Reject known vendors; prefer unnamed to wrongly named |
| Use `.get("field", "")` on extraction output | The key is present and null far more often than absent, and a default never sees null. Use `or ""` |
| Trust an institution name when one upload reports several | Disagreement means at least one is a guess. Re-read them from the pages |
| Read transactions without thinking about `include_deleted` | It defaults to excluding dropped lines. Pass True only to show someone what they removed, or to count a cascade |
| Soft-delete a statement or an account | The PDF in Storage is the undo. Soft state there infects account lists, queues, search scoping, and gap detection for no gain |
| Leave a rejected extraction in the table "filtered out" | Delete it. Invisible-but-present means nobody can act on it and the empty account never goes away |
| Delete an account that carries a characterization | `_reason_to_keep()` spares it. Ownership, character, purpose, and notes are attorney work, not import output |
| Overwrite an extracted value in place | Correct it through `correct_transaction()` — the flag trail is what makes the figure defensible |
| Convert `Decimal`/`date` to `str` before `repo.update()` | Pass the native type; `json_safe` handles it, and converting early corrupts the returned model |
| Add an `UNRECONCILED` flag on re-check | Replace the existing one, or a corrected statement keeps claiming it is out of balance |
| Infer account ownership from `opposing_party_id` being null | Read `ownership`. Null-means-ours cannot express *joint*, and joint decides whether an asset divides |
| Assume a missing institution means the PDF is unreadable | It usually means the name is only in the letterhead graphic. Ask the page (`pdf_service.ask_page`) |
| Delete a financial account before moving its statements | `financial_account_statements` cascades on account delete — the evidence goes with it |
| Take `files[0]` from a drop event | Statements arrive as a stack. Dropping the rest silently is the worst failure mode: nothing looks wrong |
| Construct a Bates number for an unstamped page | Leave it null. A cited number that is not on the page is worse than no citation |
| Ask the model for a Bates number when a series was detected | The pattern owns the field — the model's value is discarded, not used as a fallback |
| Derive a statement's page range from its transactions | Use the span. A worksheet or disclosures page carries no lines and is not missing |
| Put a cheap model on an extraction that becomes evidence | Chain order here is measured accuracy. A model that invents JSON keys and mis-states amounts costs more than it saves |
| Record only the profile name as an extraction's provenance | A chain has several vendors and a chunked read can use more than one. Use `complete_detailed()` and keep the per-pass record |
| Index into `response.content[0]` on an Anthropic reply | Extended thinking puts a `ThinkingBlock` first. Select by block type — `_anthropic_text` |
| Test an Anthropic model family with `"-5" in model` | That matches `claude-haiku-4-5-…` too, and silently drops `temperature` on tasks that ask for 0.0. Use `_anthropic_takes_temperature` |
| Read a Gemini `504 DEADLINE_EXCEEDED` as a network fault | It is usually our own deadline. Gemini enforces it server-side and reports it in Google's error format |
| Set one global timeout for every task | A long answer needs a long deadline, and a short one silently selects for the model that quits earliest. Use `timeout_seconds` on the profile |
| Return `""` for a page OCR could not read | Leave the marker. A page known to be missing and a page that was blank are not the same fact |
| Repair only the nulls when a batch comes back with missing dates | A null marks a degraded call; the dates it did produce are equally suspect. Re-read the whole batch's date column |
| Match a re-read line by its description or amount | Both repeat constantly on a statement. Match by printed position |
| Assume a checks-summary table repeats debits already listed | On many statements it is the only place the check appears. Skip rows with no printed amount; dedupe the rest |
| Read a multi-column table straight down one column | Statement tables are column groups read left to right; the extracted text interleaves them |
| Raise `max_tokens` when an extraction comes back short | Check what it actually used first. A model that stops at a seventh of its budget did not run out of room — split the work instead |
| Trust a well-formed extraction because it parsed | Valid JSON says nothing about completeness. Compare it against the totals the statement prints for itself |
| Gate Bates detection on perfect one-per-page steps | That rejects exactly the incomplete productions worth flagging. Gate on monotonicity |
| Export the page the user is looking at | An export covers every matching line. A summary that stopped at the page size looks complete and is wrong |
| Put a preamble above a CSV's header row | CSV is the clean extraction. The caption and the notice belong in the exhibit formats |
| Interpolate a value into a caption before parsing its markup | The value gets read as markup — a blank rule of underscores becomes an underline marker. Parse, then substitute |
| Print "None" onto a caption a matter cannot fill | Print the blank rule and return a warning. The attorney wants numbers before a cause number exists |
| Hardcode the caption in a renderer | It is a template (`_SYSTEM_CAPTION`). Firms disagree about wording, never about how .docx stores bold |
| Sum raw amounts on the FIS without checking the account type | A card purchase is positive by the printed convention. Debit and credit spending then cancel out. `household_amount()` |
| Average a quarterly or annual bill over the report window | It buys coverage past the window, so the figure moves every time the report runs. Trailing twelve months over twelve |
| Divide by "months that had activity" | An annual premium would read as its full amount per month. Always the window's months |
| Sort a category tree by `display_order` alone | It orders siblings. Two branches sharing a number strands one branch's children under the other. Use `get_ordered()` |
| Indent an `<option>` with ordinary spaces | HTML collapses them and the list renders flat. `lib/categories.categoryLabel` uses non-breaking spaces |
| Let an FIS window include a part-month | "Average monthly" over three-and-a-bit months cannot be explained on the stand |
| Let a rule overwrite a category a person set | Fill the empty and the machine-filed only. A tool that undoes judgment is worse than no tool |
| Assign a category without recording what assigned it | The review queue, the undo, and the answer to "why is this here?" all depend on it |
| Substring-match a flattened description without checking word boundaries | `TARGET` matches `STARGETTER LLC`. Flattening removes the separators that told you where words end |
| Order rules by priority alone | `WALMART` and `WALMART PHARMACY` at equal priority: the longer pattern must be tried first or the specific rule never fires |
| Let a rule-loading failure break an ingest | The statement is evidence; filing it is convenience. Log and carry on unfiled |
| Format a CSV amount as currency | A spreadsheet reads `-$2,500.00` as text and will not sum it. Currency is for the exhibit formats; the CSV carries the raw figure |
| Ship a summary that cannot name the documents behind it | The notice promises the records are available; `sources` says which. Group by upload, not statement |
| Sort Bates stamps as strings | "KF 9" follows "KF 10" alphabetically and precedes it on the production. Sort on the numeric tail |
| Ship an exhibit without the Rule 1006 notice in the file | On screen it is no use once the document has left. A wrong date inside a period reconciles cleanly and reaches an exhibit unflagged |
| Require a movement verb before reading a masked number | Nobody masks a confirmation number. `Deposit from … XXXXXXX3640` was invisible for want of the word "transfer" |
| Add a parser route without adding its database term | The gate is pushed to the query. A line the regex would accept is never fetched to be parsed |
| Read a transfer's direction from the words "to" and "from" | One description carries both. The sign of the amount says which way the money went |
| Read a trailing digit run on a PAYMENT as an account number | On a transfer it is a bank convention; on a payment it is a confirmation number. `Zelle Payment To Kathy Gunn 20928990159` became an undisclosed account belonging to Kathy Gunn |
| Decide from the text whether a payee is a creditor | "Payment To Mr. Cooper" and "Payment To Frontier" are the same sentence. It comes from the category a person filed it under, or a recorded ruling — nowhere else |
| Put an unclassified payee in the exhibit | It asserts of a water utility what it asserts of American Express. Candidates are a screen-only work queue |
| Seed a `not_creditor` ruling | It hides an account permanently and silently. Suppression is always somebody's recorded decision |
| Key an institution on the ABA inside an ACH trace number | Those nine digits are the CREDITOR's bank. It reports an undisclosed account at Wells Fargo because the client pays an Amex bill |
| Let a scraped payee key be aggressive enough to merge two names | Two groups for one payee is cosmetic and collapses on the first ruling. Two creditors merged into one row is not recoverable |
| Strip a payee phrase that leaves an empty string | The line is then dropped with no trace. "Venmo Payment" keeps its trailing word so the row stays visible |
| Throw a failed response's raw body as the error | The prose the handler wrote is inside `detail`. `errorMessage()` unwraps it; ~90 display sites showed braces before it existed |
| `window.open` after an `await` | The popup blocker judges a tab by whether a real click opened it. Open it synchronously, then set `location` |
| Answer "the PDF is missing" with one 404 | Never stored, purged, and storage-is-down call for three different next moves from whoever clicked |
| Retry a temperature-0 call without changing the prompt | It returns the identical reply. The retry note is the mechanism, not the second call |
| Give a truncated reply the malformed-JSON retry note | It ran out of room; escaping advice answers a different failure. Name the one that happened |
| Split a range before asking it again | Splitting costs a call per half. The second ask recovered the measured case twice out of two |
| Discard a split's successful half when its sibling fails | Return the pages that failed, not an exception. The loss is reported page by page |
| Let one unparseable chunk fail the whole ingest | Four accounts that read perfectly are lost with it. Record the pages, flag the statement, carry on |
| Log an LLM response to diagnose a parse failure | It is a transaction register. Log the error offset and the length; the text is in the provenance |
| Treat a malformed JSON reply as a vendor failure | The vendor answered. `llm_service` never retries a candidate, so the caller that needed JSON asks again |
| Match a restarted page number as `PAGE\s1` | It matches inside "Page 10", "Page 19" and "Page 142". One long statement then reads as a dozen |
| Block an upload that looks like several statements | A combined statement is ordinary input. Warn, name the count, and say to split it if the result is wrong |
| Let a pre-flight warning raise | The job is queued before the survey runs. A failed warning must not read as a failed upload |
| Bind `pdf_service` at import in `statement_service` | Every use there resolves at call time; the suites replace the singleton on its own module and would not be seen |
| Assume a new account starts on a fresh page | A combined statement runs registers together. One page can close one account, hold a whole second, and open a third |
| Slice a multi-account document by page range | The pass is then told "these pages belong to" an account that owns only part of one. Cut at the printed header |
| Treat two statements sharing a page as a misreading | That is a seam on a combined statement. A phantom shares more than a boundary, or names a different bank |
| Requeue one statement's PDF without discarding its siblings | One upload can hold five accounts. Re-reading recreates all five, and the four left behind become duplicates |
| Delete before confirming the source PDF can be retrieved | A retry against a purged document then destroys the evidence it meant to re-read |
| Open a statement's PDF at page 1 | One upload routinely holds a whole production. Send the first transaction page as a `#page=` hint — approximate, and the caller says so |
| Read a routing number as an account number | An ABA is nine checksummed digits identifying a BANK. Reporting "UBS ····7993" as an account is a confident-wrong finding |
| Assume a wire names the account it came from | It names the bank. That is why institutions are a separate section keyed on the ABA |
| Report an inferred institution as though it were read off the page | Flag it. The dagger is the difference between a finding and an assertion |
| Take a model's null account number at face value | It is the dedup key. Find it by pattern — the one long digit run on every page — or a form the model reads half the time opens one account per statement |
| Let the pattern override an extracted account number | Unlike a Bates stamp, the number is printed in prose too. Disagreement is a real conflict: keep the extraction and flag it |
| Derive an account number from a single page | Repetition is the whole signal. One page cannot demonstrate it |
| Scope an account-number search to a statement's transaction pages | Those are not its extent — disclosures and continuation pages carry no lines. One statement in a document means the whole document |
| Let a narrow LLM lookup answer without the bounds the pattern uses | It chose a 20-digit barcode twelve times. Same length limits, plus "does it repeat across pages" |
| Require an account number on every page | A cover sheet, a terms-of-service letter, or an insert carries none, and one such page vetoes the real answer. Most pages wins |
| Accept a model's account number without checking it against the page | Compare each printed run separately. An answer that is not printed there was invented |
| Store a masked account number with no digits in it | It is the caption, scraped. `"Account Number:"` reached production twice |
| Trust `json.loads(llm_response)` directly | Strip markdown fences first — LLMs wrap JSON in ``` ```json ``` ``` despite being told not to |
| Run a job to completion on the worker's main loop | One long statement then blocks every other job and the CRM tick. Claim to capacity, run in the pool |
| Share one `SupabaseManager` across pool threads | It is not thread-safe. A manager per job, and it is cheap to make |
| Gate a drop zone's busy state on the job | The job does not exist until every upload finishes. A dozen files is 10–15 seconds of a screen that looks untouched, and the natural response is to drag them again |
| Guard re-entry on React state | State does not update until the next render, so two quick drops both read the old value and both run. Use a ref, set on the same tick |
| Take a re-drag as a new upload | It is somebody unsure the first one landed. Refuse it and say so — silence is what caused it |
| Set a busy ref without releasing it in `finally` | One unexpected throw disables the drop zone for the life of the page, curable only by a reload nobody would think to try |
| Upload one file and await it before sending the next | The pool then has one job to run whatever was dropped. Queue the whole stack, then wait |
| Do LLM or OCR work inside a request handler | Queue a job and poll — haproxy cuts long requests with a 504 and no server-side error (§11a) |
| Add a model field without a migration in the same change | The startup schema check will log an `ERROR`, but the insert is already broken |
| Persist PDF-extracted text straight from PyMuPDF | It can contain NULs; Postgres rejects the row (`22P05`). `pdf_service` sanitizes — use its output |
| Decide "opposing" without knowing our client | `intake_service`/`pleading_service` take the client name; a pleading names *our* attorney too |
| Assume a bank masks an account with X's | Chase uses dots and no space (`Chk ...9323`). Match a run of mask characters, not a literal XX |
| Match people on surname alone | Adverse parties share surnames — require first **and** last (`_match_confidence`) |

---

*End of CLAUDE.md — keep this file current as conventions evolve. When a pattern changes, update the rule here and search the codebase for any old instances.*

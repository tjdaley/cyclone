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

- Object keys: `description`, `extends`, `chain`, `temperature`, `top_p`, `max_tokens`, `vision`. Unknown keys are rejected at load. Top-level keys starting with `_` are ignored — that's how you comment in JSON.
- **Convention:** `default`, `fast`, and `vision` are the physical model chains; every task profile `extends` one of them. Retune a task by changing what it extends.
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
| Background job queue (`jobs` table + worker) | ✅ Built — matter intake only; see §11a |
| Schema drift check at startup | ✅ Built (`util/schema_check.py`) |
| Standard privileges/objections lookup tables | ✅ Seeded |
| Privacy policy + terms of use pages (for Google OAuth cert) | ✅ Built |
| Conflict checking | ⏳ Done manually **outside** Cyclone today. Planned home: at the `leads` table, so every promoted client has passed it. `conflict_check_run` already exists in the lead action enum |
| Phase 2 conflict checking (pg_trgm) | ⏳ SQL ready, Python wiring uses substring match only |
| Re-extract / delete a pleading | ❌ Not started — `raw_text` is stored, so re-extraction needs no re-upload |
| Moving the intake PDF onto the matter at commit | ❌ Not started — lands at `intake/{job_id}.pdf`; `StorageService.move()` exists |
| Client Portal (separate from staff) | ❌ Not started — `client` role exists but redirects to `/access-denied` |
| PDF bill generation (WeasyPrint) | ❌ Not started |
| Stripe checkout / webhooks | ❌ Keys configured, no handler |
| Email notifications | ❌ Not started |
| Fee agreement templates + e-sign | ❌ Model exists; no UI or workflow |
| Client intake form / StepWizard | ❌ Not started |
| Shared components (DataTable, ConfirmDialog) | ❌ Pages use inline tables |
| Test suite | ❌ No unit or integration tests |

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
| Trust `json.loads(llm_response)` directly | Strip markdown fences first — LLMs wrap JSON in ``` ```json ``` ``` despite being told not to |
| Do LLM or OCR work inside a request handler | Queue a job and poll — haproxy cuts long requests with a 504 and no server-side error (§11a) |
| Add a model field without a migration in the same change | The startup schema check will log an `ERROR`, but the insert is already broken |
| Persist PDF-extracted text straight from PyMuPDF | It can contain NULs; Postgres rejects the row (`22P05`). `pdf_service` sanitizes — use its output |
| Decide "opposing" without knowing our client | `intake_service`/`pleading_service` take the client name; a pleading names *our* attorney too |
| Match people on surname alone | Adverse parties share surnames — require first **and** last (`_match_confidence`) |

---

*End of CLAUDE.md — keep this file current as conventions evolve. When a pattern changes, update the rule here and search the codebase for any old instances.*

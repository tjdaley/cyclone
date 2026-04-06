# CLAUDE.md — Cyclone Project Conventions
> This file is read by Claude Code at the start of every session.
> Follow every rule here without exception unless the developer explicitly overrides one in the current session.

---

## 1. Project Overview

**Cyclone** is a legal practice management platform with a React frontend and FastAPI backend, persisted to Supabase (PostgreSQL). The full spec lives in `CYCLONE_PRD.md` at the repo root. Read it before beginning any non-trivial feature work.

**Repo:** https://github.com/tjdaley/cyclone.git  
**Stack:** React (Vite) · FastAPI · Supabase (PostgreSQL) · Docker  
**Python:** 3.11+  
**Node:** 20+

---

## 2. Repository Layout

```
cyclone/
├── app/                        # FastAPI backend
│   ├── main.py
│   ├── dependencies.py         # Shared Depends() — get_db_manager, get_current_user, require_role
│   ├── util/
│   │   ├── settings.py         # Settings(BaseSettings) singleton — see §5
│   │   └── loggerfactory.py    # LoggerFactory — see §6
│   ├── db/
│   │   ├── supabasemanager.py  # DatabaseManager ABC + SupabaseManager — see §7
│   │   ├── models/             # Pydantic domain + InDB models — see §8
│   │   └── repositories/       # BaseRepository[T] subclasses — see §8
│   ├── routers/                # FastAPI route handlers (thin — delegate to services)
│   ├── services/               # Business logic, LLM calls, PDF, Stripe
│   └── schemas/                # Pydantic request/response schemas (separate from DB models)
├── frontend/                   # React + Vite
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   ├── context/
│   │   └── lib/                # API client, Supabase client, helpers
│   └── vite.config.ts
├── docker-compose.yml
├── docker-compose.override.yml # Dev overrides — committed, no secrets
├── .env.example                # All keys, no values — committed
├── CYCLONE_PRD.md              # Full product spec
└── CLAUDE.md                   # This file
```

---

## 3. Golden Rules

These override everything else, including your own judgment about "better" patterns.

1. **Read before you write.** Before editing any existing module, read it fully. Never assume file contents match what you expect.
2. **No new patterns without justification.** If the codebase already has a pattern for something (logging, DB access, settings), use it. Do not introduce a second way to do the same thing.
3. **No raw SQL from the frontend.** All data mutations and queries go through FastAPI endpoints.
4. **No new `Settings()` instantiations.** Import `settings` from `app.util.settings` everywhere except `supabasemanager.py`, which has its own local `SETTINGS = Settings()` for historical reasons.
5. **No direct `logging.getLogger()` calls.** Use `LoggerFactory.create_logger(__name__)` exclusively.
6. **No LLM calls outside `app/services/llm_service.py`.** All AI completions are dispatched through the `LLMService` singleton.
7. **No `supabase.create_client()` outside `supabasemanager.py`.** All DB access goes through repository classes.
8. **Test before declaring done.** Run the relevant test suite or perform a manual smoke test against the dev Docker stack before marking a task complete.

---

## 4. Coding Standards

### Python
- **Style:** PEP 8; 4-space indent; max line length 120
- **Type hints:** Required on all function signatures (args and return types)
- **Docstrings:** Required on all public methods; use the existing `:param name: ... :type name: ... :return: ... :rtype: ...` format found in `supabasemanager.py`
- **Imports:** stdlib → third-party → local app; alphabetical within each group; no wildcard imports
- **f-strings:** Preferred for interpolation in application code; use `%s` format args in log calls (lazy evaluation)
- **Exception handling:** Catch the narrowest exception possible; always log at `ERROR` or higher before re-raising

### TypeScript / React
- **Style:** ESLint + Prettier defaults; 2-space indent
- **Component files:** One component per file; filename matches component name (PascalCase)
- **Hooks:** Custom hooks in `src/hooks/`; prefix with `use`
- **No inline styles.** Use Tailwind utility classes or CSS custom properties only
- **No direct Supabase data queries from components.** All data fetching goes through `src/lib/api.ts` (the FastAPI client wrapper)
- **Supabase JS client** (`src/lib/supabaseClient.ts`) is used only for auth session management

---

## 5. Settings (`app/util/settings.py`)

- `Settings(BaseSettings)` is a Pydantic settings class loaded from `.env`
- The module-level singleton `settings = Settings()` is the **only** instance used throughout the app (except `supabasemanager.py`)
- **Import pattern:**
  ```python
  from app.util.settings import settings
  ```
- **Dynamic access:** Use `settings.getattr(item, default)` — do not use `getattr(settings, item)` directly
- **Adding new fields:** Add to the `Settings` class with a safe default (usually `""` or `None`); add the key to `.env.example` in the same PR
- **Never hardcode** API keys, URLs, or environment-specific values anywhere in application code

### Fields Claude Code Must Know About

| Field | Type | Purpose |
|-------|------|---------|
| `supabase_url` | `str` | Supabase project URL |
| `supabase_service_role_key` | `str` | Used by backend only — never expose to frontend |
| `supabase_anon_key` | `str` | Used by frontend Supabase JS client |
| `llm_vendor` | `str` | Active LLM vendor: `"anthropic"`, `"gemini"`, `"openai"`, `"groq"`, `"deepseek"` |
| `llm_fast_vendor` | `str` | Vendor for latency-sensitive calls |
| `llm_temperature` | `float` | Default: `0.1` |
| `llm_top_p` | `float` | Default: `0.1` |
| `stripe_secret_key` | `str` | Stripe server-side key |
| `stripe_publishable_key` | `str` | Safe to expose via `GET /api/config` |
| `time_increment_options` | `list[float]` | Valid billing time increments, e.g. `[0.1, 0.25, 0.5, 1.0]` |
| `default_refresh_trigger_pct` | `float` | Default retainer refresh threshold: `0.40` |
| `is_development` | `bool` | True in dev; gates debug behavior |
| `log_level` | `str` | Default: `"WARNING"` |
| `log_format` | `str` | Python `logging` format string |

---

## 6. Logging (`app/util/loggerfactory.py`)

### Usage — Required Pattern

Every module that needs logging must declare a module-level logger:

```python
from app.util.loggerfactory import LoggerFactory

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
BillingEntryRepository, StaffRepository, ...  ← domain repos
        ↑ injected into
Service classes                    ← business logic
        ↑ called by
Route handlers (via Depends())
```

### SupabaseManager Rules

- `SupabaseManager` uses `supabase_service_role_key` — it bypasses Supabase RLS. Access control is enforced at the FastAPI route layer via `require_role()`.
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
from app.db.repositories.base_repo import BaseRepository
from app.db.models.my_entity import MyEntityInDB
from app.db.supabasemanager import DatabaseManager

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
# app/dependencies.py
from app.db.supabasemanager import SupabaseManager

def get_db_manager() -> SupabaseManager:
    return SupabaseManager()
```

```python
# In a route handler
from app.dependencies import get_db_manager
from app.db.repositories.billing_entry import BillingEntryRepository

@router.get("/billing/{matter_id}")
def get_entries(matter_id: int, manager = Depends(get_db_manager)):
    repo = BillingEntryRepository(manager)
    return repo.get_by_matter(matter_id)
```

---

## 8. Model Conventions (`app/db/models/`)

Follow the pattern in `app/db/models/attorney.py` exactly:

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
- `model_config = ConfigDict(from_attributes=True)` goes on `InDB` only
- Do not use `orm_mode = True` (deprecated Pydantic v1 syntax)

### Staff Model (replaces Attorney)

The starter `Attorney` model is an example only. The real user model is `StaffMember`:

```python
# app/db/models/staff.py
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime

StaffRole = Literal["attorney", "paralegal", "admin"]

class StaffMember(BaseModel):
    supabase_uid: str = Field(..., description="Supabase auth UID — foreign key to auth.users")
    role: StaffRole = Field(..., description="Staff role")
    email: str = Field(..., description="Work email")
    first_name: str = Field(..., description="First name")
    last_name: str = Field(..., description="Last name")
    slug: str = Field(..., description="URL-safe unique identifier")
    # ... bar admissions, office_id, telephone, etc.

class StaffMemberInDB(StaffMember):
    id: int = Field(..., description="Primary key")
    created_at: datetime = Field(..., description="Set by database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by database")
    model_config = ConfigDict(from_attributes=True)
```

Delete `app/db/models/attorney.py` and `app/db/repositories/attorney.py` once `StaffMember` is live and tested.

---

## 9. FastAPI Route Conventions

- Routes are **thin** — they validate input, call a service method, and return a response. No business logic in route handlers.
- All routes are versioned: `/api/v1/...`
- Utility routes (health check, config): `GET /api/health`, `GET /api/config`
- Use `Depends(require_role([...]))` on every protected route
- Return Pydantic response schemas (from `app/schemas/`) — never return raw `InDB` models directly to the frontend

### Middleware Stack (in order)
1. `CORSMiddleware`
2. `AuthMiddleware` — validates Supabase JWT; injects `supabase_uid` and `role` into `request.state`
3. Route-level `Depends(require_role([...]))`

---

## 10. LLM Service (`app/services/llm_service.py`)

All LLM calls go through a single `LLMService` class. No other file in the codebase imports an LLM SDK directly.

- Dispatches on `settings.llm_vendor`
- Supported vendors: `anthropic`, `gemini`, `openai`, `groq`, `deepseek`
- Use `settings.llm_fast_vendor` for latency-sensitive calls (e.g., real-time billing parse)
- Always log at `DEBUG` level before and after LLM calls (prompt truncated to 200 chars if needed)
- LLM responses that should be structured data must instruct the model to return **only valid JSON with no markdown fences or preamble**; parse with `model.model_validate_json(response_text)`

### Key LLM Use Cases

| Feature | Endpoint | Notes |
|---------|----------|-------|
| Natural language billing entry | `POST /api/v1/billing/parse` | Returns preview card; not committed until attorney confirms |
| Discovery request ingestion | `POST /api/v1/discovery/ingest` | Segments and classifies raw discovery text |
| (Future) Statement parsing | `POST /api/v1/inventory/parse-statement` | Extracts asset/debt details from uploaded PDF |

---

## 11. Audit Log

Sensitive actions must write a record to the `audit_log` table **in addition to** the application log. Use a shared `AuditLogger` service for this — do not write to `audit_log` directly from route handlers.

Actions requiring an audit log entry:
- Billing entry created / edited / deleted
- Billing cycle closed
- Bill sent to client
- Fee agreement signed
- Trust ledger transaction posted
- User role changed

Schema:
```sql
CREATE TABLE audit_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   TIMESTAMPTZ DEFAULT now(),
    supabase_uid TEXT,
    action       TEXT NOT NULL,      -- e.g. 'billing_entry.created'
    entity_type  TEXT NOT NULL,      -- e.g. 'billing_entry'
    entity_id    TEXT,
    before_json  JSONB,
    after_json   JSONB
);
```

---

## 12. Frontend Conventions

### API Calls
All backend calls go through `src/lib/api.ts`. Never call `fetch()` or `axios` directly from a component.

```typescript
// src/lib/api.ts
const BASE_URL = import.meta.env.VITE_API_BASE_URL;

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const session = await supabase.auth.getSession();
  const token = session.data.session?.access_token;
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
```

### Dual-Density Layout
The `AppShell` component sets a `data-density` attribute on `<body>` based on the user's role:
- `data-density="relaxed"` — client portal (generous spacing, larger type)
- `data-density="compact"` — staff portal (tighter grid, more info per viewport)

CSS custom properties and Tailwind variants key off this attribute to adjust spacing and font sizes globally. Do not hardcode density-specific styles in individual components.

### Supabase JS Client
Used **only** for auth:
```typescript
// src/lib/supabaseClient.ts
import { createClient } from '@supabase/supabase-js';
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY
);
```
Do not use `supabase.from(...)` for data queries from the frontend.

---

## 13. Docker

### Base (`docker-compose.yml`)
```yaml
services:
  api:
    build: ./app
    ports: ["8000:8000"]
    env_file: .env

  frontend:
    build: ./frontend
    ports: ["3000:80"]
    env_file: .env.frontend
```

### Dev Overrides (`docker-compose.override.yml`)
```yaml
services:
  api:
    volumes:
      - ./app:/app
    environment:
      - IS_DEVELOPMENT=true
      - LOG_LEVEL=DEBUG
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    volumes:
      - ./frontend:/app
      - /app/node_modules
    environment:
      - VITE_API_BASE_URL=http://localhost:8000
```

Run dev stack: `docker compose up` (override is applied automatically).  
Run production stack: `docker compose -f docker-compose.yml up`.

### Startup Checks (`app/main.py`)
On startup:
1. Pydantic validates all `Settings` fields — raises `ValidationError` before accepting requests if required fields are missing
2. `SupabaseManager.__init__` raises `ValueError` if `supabase_url` or `supabase_service_role_key` are empty
3. Log at `INFO`: `"Cyclone API started | env=%s llm_vendor=%s log_level=%s"`

---

## 14. Environment Variables

| Variable | Used By | Notes |
|----------|---------|-------|
| `SUPABASE_URL` | Backend + Frontend | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend only | Never expose to frontend |
| `SUPABASE_ANON_KEY` | Frontend only | Subject to RLS |
| `SUPABASE_JWT_SECRET` | Backend (auth middleware) | |
| `LLM_VENDOR` | Backend | Active vendor |
| `ANTHROPIC_API_KEY` | Backend | If vendor = anthropic |
| `GEMINI_API_KEY` | Backend | If vendor = gemini |
| `OPENAI_API_KEY` | Backend | If vendor = openai |
| `GROQ_API_KEY` | Backend | If vendor = groq |
| `STRIPE_SECRET_KEY` | Backend | Never expose to frontend |
| `STRIPE_PUBLISHABLE_KEY` | Backend → `/api/config` → Frontend | |
| `STRIPE_WEBHOOK_SECRET` | Backend | Webhook validation |
| `IS_DEVELOPMENT` | Backend | Gates debug behavior |
| `LOG_LEVEL` | Backend | Default: WARNING |
| `VITE_SUPABASE_URL` | Frontend build | Baked in at build time |
| `VITE_SUPABASE_ANON_KEY` | Frontend build | |
| `VITE_API_BASE_URL` | Frontend build | |

`.env.example` must be kept current. Every new env var added to the codebase must appear in `.env.example` in the same commit.

---

## 15. What NOT to Do

| Don't | Do Instead |
|-------|-----------|
| Call `logging.getLogger()` directly | `LoggerFactory.create_logger(__name__)` |
| Instantiate `Settings()` in app code | `from app.util.settings import settings` |
| Call `supabase.create_client()` outside `supabasemanager.py` | Use a repository class |
| Pass a JSON string to `repo.insert()` | Pass `.model_dump()` |
| Write business logic in route handlers | Write it in a service class |
| Query Supabase tables from React components | Go through `src/lib/api.ts` → FastAPI |
| Use `orm_mode = True` | Use `ConfigDict(from_attributes=True)` |
| Log PII (names, amounts, case facts) | Log entity IDs only |
| Import LLM SDKs outside `llm_service.py` | Call `llm_service.complete(...)` |
| Hardcode environment-specific values | Use `settings.*` |

---

*End of CLAUDE.md — keep this file current as conventions evolve.*
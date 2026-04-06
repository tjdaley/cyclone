# Cyclone — Product Requirements Document
**Version:** 1.3  
**Author:** Thomas J. Daley, J.D.  
**Repo:** https://github.com/tjdaley/cyclone.git  
**Stack:** React · FastAPI · Supabase (PostgreSQL) · Docker  
**Status:** Pre-development / Claude Code Input

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [User Roles & Access Control](#2-user-roles--access-control)
3. [Core Feature Modules](#3-core-feature-modules)
4. [UI Layout & Component Structure](#4-ui-layout--component-structure)
5. [Color Palette & Design Tokens](#5-color-palette--design-tokens)
6. [Database Access Patterns](#6-database-access-patterns)
7. [Runtime Parameters & Environment Configuration](#7-runtime-parameters--environment-configuration)
8. [Logging Strategy](#8-logging-strategy)
9. [API Integration — LLM & FastAPI](#9-api-integration--llm--fastapi)
10. [Docker & Deployment](#10-docker--deployment)
11. [Future Features (Stub Only)](#11-future-features-stub-only)

---

## 1. Project Overview

**Cyclone** is a web-based legal practice management platform serving law firms and their clients. It has two distinct interface modes: a **Client Portal** and a **Staff Portal** (attorneys, paralegals, admins). Both share the same backend but present dramatically different UX densities and workflows.

### Core Goals
- Streamline client onboarding, conflict checking, and fee agreement execution
- Provide attorneys and paralegals with fast, flexible billing entry — including a natural-language/free-text billing interface powered by an LLM
- Expose billing statements to clients via a portal with integrated Stripe payment processing
- Support multi-client billing splits for appointed matters (discovery master, mediator, etc.)
- Provide a discovery response workflow where clients contribute directly to interrogatory responses, witness lists, RFAs, and RFPs
- Run entirely inside Docker with dev/prod environment overrides

### What Cyclone Is Not (Yet)
- Not a general ledger or trust accounting system (GL integration is a future stub)
- Not a full inventory & appraisement tool (future feature — architecture must accommodate it)
- Not a case management / docket system (events/deadlines are read-only views from manual entry or future calendar integration)

---

## 2. User Roles & Access Control

Authentication is handled by **Supabase Auth**. Users log in with existing credentials (Google OAuth, email/magic link, etc.).

### 2.1 Staff Model

The `Attorney` model in the starter repo is illustrative only. Replace it with a unified **`Staff`** model with a `role` field:

```python
# app/db/models/staff.py
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum

class StaffRole(str, Enum):
    attorney  = "attorney"
    paralegal = "paralegal"
    admin     = "admin"

class StaffMember(BaseModel):
    name: FullName                        # reuse FullName from attorney.py
    role: StaffRole
    office_id: int
    email: str
    telephone: str
    slug: str
    bar_admissions: List[BarAdmission] = []  # empty for non-attorneys

class StaffMemberInDB(StaffMember):
    id: int
    supabase_uid: str                     # links to auth.users
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
```

### 2.2 Role Table

A `user_roles` table maps a Supabase `auth.users` UID to either `client` or a staff role:

| `supabase_uid` | `role` | `staff_id` | `client_id` |
|----------------|--------|------------|-------------|
| uuid | `attorney` | FK → staff | null |
| uuid | `client` | null | FK → clients |

### 2.3 Role Enforcement

- FastAPI routes use a `require_role(["attorney", "admin"])` dependency via `Depends()`
- Supabase RLS policies mirror role logic as a backstop
- React conditionally renders nav items and portal mode based on role stored in auth session context

### 2.4 Role Summary

| Role | Portal | Description |
|------|--------|-------------|
| `client` | Client Portal | Sees only their own matters, bills, events, and discovery tasks |
| `attorney` | Staff Portal | Full billing, matter management, and discovery access |
| `paralegal` | Staff Portal | Same as attorney except cannot approve bills or sign fee agreements |
| `admin` | Staff Portal | Full system access: settings, user management, fee templates, reports |

---

## 3. Core Feature Modules

### 3.1 Client Onboarding (Client-Facing)

**3.1.1 Intake Form**
- Multi-step form: contact info, matter type, opposing party, children (if applicable), prior counsel, referral source
- Form state persisted in Supabase as a draft until submitted; allows resume on return visit
- On submission, triggers a backend task to run the conflict check and notify the responsible attorney

**3.1.2 Conflict Check**
- Queries `matters`, `clients`, and `opposing_parties` for name matches (fuzzy match via `pg_trgm`)
- Results surfaced to an attorney or admin for review before the matter is activated
- Client sees a "pending review" status; conflict details are not disclosed to the client

**3.1.3 Fee Agreement**
- Attorney configures a fee agreement template per matter type in the admin UI
- Template pre-populated with client name, matter type, retainer amount, refresh trigger percentage (default: 40%), and hourly rates
- Client reviews and executes via electronic signature (Phase 1: checkbox acknowledgment + timestamp; Phase 2: DocuSign or similar)
- Executed agreement stored as a PDF snapshot in Supabase Storage, linked to the matter record

---

### 3.2 Client Portal (Client-Facing)

**3.2.1 Billing Review**
- Available after billing cycle status = `closed`
- Itemized bill: date, timekeeper, description, hours/units, rate, amount
- Stripe-hosted payment link embedded in portal for direct payment
- PDF bill downloadable from portal

**3.2.2 Events & Deadlines**
- Read-only ascending list of upcoming hearings, deadlines, and appointments
- Past events hidden by default; toggle to show
- Data source: `matter_events` table (manual entry; future: calendar sync)

---

### 3.3 Staff Portal — Billing

**3.3.1 Billing Entry — Form Interface**
- Autocomplete selectors: Client → Matter → Timekeeper
- Entry type: `time` | `expense` | `flat_fee`
  - **Time:** Duration in increments defined in `Settings.time_increment_options` (e.g., `[0.1, 0.25, 0.5, 1.0]`); optional non-billable flag; rate from matter rate card (overridable); optional fixed-amount override
  - **Expense:** Amount, vendor/description, optional receipt upload
  - **Flat fee:** Fixed dollar amount, description; hours field hidden
- **Client Financial Balance Widget** shown on form:
  - Formula: `trust_balance − sum(unbilled time entries) − sum(unbilled expense entries)`
  - 🔴 Red: balance < $0
  - 🟡 Yellow: balance < (retainer × refresh_trigger_pct)
  - 🟢 Green: otherwise
  - Refreshes on client/matter selection and after each committed entry

**3.3.2 Billing Entry — Natural Language Interface**
- Free-text field, e.g.: `"bill .25 to Anna Jones Divorce for drafting initial petition for divorce"`
- Input sent to the configured LLM (see §9.2); structured billing entry returned for attorney review
- Attorney reviews parsed entry in a confirmation card → **Commit** writes to DB; **Edit** pre-populates the form
- Parse failure returns a validation message with a suggestion to clarify
- Keyboard shortcut (`/`) focuses this field from anywhere in the billing view

**3.3.3 Billing Review & Edit**
- Dual views: **By Timekeeper** and **By Matter**
- Inline editing of description, duration, rate, billable flag
- Bulk actions: mark as billed, delete, reassign matter
- Filters: date range, entry type, billable/non-billable, billing cycle status

**3.3.4 Bill Production**
- Attorney or admin triggers bill generation for a matter + billing cycle
- Server compiles unbilled entries, applies billing splits, renders PDF (WeasyPrint), stores in Supabase Storage
- Bill emailed to client(s) and made available in the client portal automatically
- Stripe payment link appended to emailed and portal-hosted PDF

---

### 3.4 Matter Configuration (Attorney/Admin)

Each matter contains:
- `matter_id`, `matter_name`, `matter_type`, `status`
- `originating_attorneys`: `[{staff_id, split_pct}]` — must sum to 100%
- `billing_review_staff_id`: single attorney responsible for bill approval
- `rate_card`: hourly rates by staff role or individual staff member
- `retainer_amount`, `refresh_trigger_pct` (default: 0.40)

**Billing Splits**
Default: one client, 100% responsibility. For appointed matters (discovery master, mediator, intervenor scenarios): a `billing_splits` table holds `{matter_id, client_matter_id, split_pct}` — must sum to 100%. When a billing entry is posted against a split matter, the system creates proportional charge records against each `client_matter_id`.

---

### 3.5 Discovery Response Module (Client + Attorney/Paralegal)

**3.5.1 Ingestion**
- Attorney or paralegal uploads or pastes opposing counsel's discovery requests
- LLM parses and segments into individual `discovery_requests` records: type (`interrogatory`, `rfa`, `rfp`, `witness_list`), request number, source text

**3.5.2 Client Tasks**
- **Witness Lists:** Add witnesses (name, address, relationship, expected testimony)
- **Interrogatories:** Read each; type draft response
- **RFAs:** Read each; select Admit / Deny / Lack Sufficient Information; optional explanatory note
- **RFPs:** Indicate whether responsive documents may exist; optionally upload documents

**3.5.3 Attorney/Paralegal Review**
- Per item: enter objection, assert privilege, add interpretive note, edit client response, mark as final
- Final responses exportable to formatted Word or PDF for service

---

## 4. UI Layout & Component Structure

### 4.1 Dual-Density Design

| Mode | Roles | Characteristics |
|------|-------|-----------------|
| **Relaxed** (Client Portal) | `client` | Generous padding, larger type, single-column on mobile, minimal chrome, guided step-by-step flows |
| **Compact** (Staff Portal) | `attorney`, `paralegal`, `admin` | Tighter grid, data-dense tables, sidebar nav, keyboard-friendly, more info per viewport |

Layout mode set at top-level `AppShell` based on authenticated role.

### 4.2 Component Hierarchy

```
AppShell
├── AuthProvider (Supabase session context)
├── RoleRouter → ClientPortal | StaffPortal
│
├── ClientPortal
│   ├── ClientNav (top bar: logo, matter name, logout)
│   ├── OnboardingFlow
│   │   ├── IntakeForm (multi-step / StepWizard)
│   │   ├── ConflictStatus
│   │   └── FeeAgreementReview
│   ├── BillingView (read-only)
│   ├── EventsView
│   └── DiscoveryTasksView
│       ├── WitnessListEditor
│       ├── InterrogatoryResponseEditor
│       ├── RFAResponseEditor
│       └── RFPDocumentUploader
│
└── StaffPortal
    ├── StaffNav (left sidebar: collapsible icon + label)
    ├── BillingModule
    │   ├── BillingEntryForm
    │   ├── NaturalLanguageBillingInput
    │   ├── ClientBalanceWidget
    │   └── BillingReviewTable
    ├── MatterModule
    │   ├── MatterList
    │   ├── MatterDetail
    │   └── BillingSplitsEditor
    ├── DiscoveryModule
    │   ├── DiscoveryIngestion
    │   └── DiscoveryReviewBoard
    └── AdminModule
        ├── UserManagement
        ├── FeeAgreementTemplates
        └── Settings
```

### 4.3 Responsive Breakpoints

| Breakpoint | Width | Behavior |
|------------|-------|----------|
| `xs` | < 480px | Single column; client nav = bottom tab bar; staff nav = hamburger |
| `sm` | 480–768px | Two-column forms; sidebar hidden, top nav |
| `md` | 768–1024px | Sidebar visible, icon-only (collapsed) |
| `lg` | 1024px+ | Full sidebar with labels; all table columns visible |

CSS Grid + `min-width` media queries. Tailwind CSS utility classes throughout.

### 4.4 Key Shared Components

| Component | Purpose |
|-----------|---------|
| `<DataTable>` | Sortable, filterable, paginated; compact density in staff portal |
| `<StatusBadge>` | Color-coded pill for matter status, balance status, discovery item status |
| `<ClientBalanceWidget>` | Trust balance formula with red/yellow/green indicator |
| `<NLBillingInput>` | Textarea + submit, loading state, parsed-entry confirmation card |
| `<PDFDownloadButton>` | Fetches signed Supabase Storage URL, opens in new tab |
| `<StepWizard>` | Multi-step form shell with progress indicator (onboarding) |
| `<ConfirmDialog>` | Modal for destructive actions (delete entry, close billing cycle) |

---

## 5. Color Palette & Design Tokens

### 5.1 Brand Colors (KoonsFuller)

| Token | Hex | Usage |
|-------|-----|-------|
| `--color-navy` | `#003057` | Headers, sidebar background, primary buttons |
| `--color-navy-light` | `#004a8f` | Hover on navy elements |
| `--color-gold` | `#C9A84C` | Active nav, highlights, CTA borders |
| `--color-gold-light` | `#e8c97a` | Hover on gold elements |
| `--color-white` | `#FFFFFF` | Page backgrounds (client portal), card backgrounds |
| `--color-off-white` | `#F5F5F0` | Staff portal background; reduces eye strain on dense UIs |

### 5.2 Semantic Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `--color-success` | `#2D7A4F` | Green balance, positive confirmations |
| `--color-warning` | `#B87E00` | Yellow/amber balance, caution states |
| `--color-danger` | `#C0392B` | Red balance, errors, destructive actions |
| `--color-text-primary` | `#1A1A1A` | Body text |
| `--color-text-secondary` | `#5A5A5A` | Labels, helper text |
| `--color-border` | `#D4D4CF` | Table borders, input outlines, dividers |
| `--color-surface` | `#FFFFFF` | Cards and panels |
| `--color-surface-raised` | `#F0EEE9` | Nested cards, table row hover |

### 5.3 Typography

| Role | Font | Weight | Size (desktop) |
|------|------|--------|----------------|
| Display / Page Title | `Playfair Display` | 700 | 28–32px |
| Section Heading | `Inter` | 600 | 18–22px |
| Body | `Inter` | 400 | 15px (client) / 14px (staff) |
| Label / Caption | `Inter` | 500 | 12px |
| Monospace (amounts, IDs) | `JetBrains Mono` | 400 | 13px |

Sizes defined as CSS custom properties (`--font-size-body`, etc.) so density mode can override globally. Load via Google Fonts.

### 5.4 Elevation

```css
--shadow-card:    0 1px 3px rgba(0,0,0,0.10), 0 1px 2px rgba(0,0,0,0.06);
--shadow-modal:   0 10px 40px rgba(0,0,0,0.18);
--shadow-popover: 0 4px 12px rgba(0,0,0,0.12);
```

---

## 6. Database Access Patterns

### 6.1 Stack

- **Database:** PostgreSQL via Supabase (managed)
- **Backend DB client:** Synchronous `supabase-py` client using the **service role key** — bypasses RLS; access control enforced at the FastAPI route layer
- **Frontend:** `@supabase/supabase-js` with the **anon key** — used only for auth session management; subject to RLS
- **No raw SQL from the frontend** — all business logic queries go through FastAPI endpoints

### 6.2 SupabaseManager

All database access goes through `DatabaseManager` (ABC) and its concrete implementation `SupabaseManager` in `app/db/supabasemanager.py`. Do not call `supabase.create_client()` anywhere else in the codebase.

#### Initialization

`SupabaseManager.__init__` reads `supabase_url` and `supabase_service_role_key` from a locally-instantiated `Settings()`. It raises `ValueError` immediately at startup if either is empty — this is the fail-fast DB config check.

```python
# At module level in supabasemanager.py — do not replicate this pattern elsewhere
SETTINGS = Settings()
LOGGER = LoggerFactory.create_logger(__name__)

# Also at module level — sets third-party log verbosity; do not override
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("postgrest").setLevel(logging.DEBUG)
```

#### Method Reference

All methods carry `@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), retry=retry_if_exception_type(APIError))`.

| Method | Signature | Key Behavior |
|--------|-----------|--------------|
| `select_one` | `(table, result_type, condition, select_string="*") → Optional[T]` | Returns `None` on PGRST116 (no match); re-raises all other `APIError`s |
| `select_many` | `(table, result_type, condition, select_string="*", sort_by, sort_direction, start, end) → tuple[list[T], int]` | Returns `([], 0)` on empty result; second element is exact `COUNT(*)` |
| `insert` | `(table, data, result_type) → T` | **Raises `ValueError` if `data` is a string** — must always pass a `dict` |
| `update` | `(table, record_id, data, result_type) → T` | Matches on `id` field only |
| `delete` | `(table, record_id) → bool` | Always returns `True`; raises on error |
| `exists` | `(table, field, value) → bool` | Selects `id` with `count=exact`; returns `False` on PGRST116 |

#### Critical Rule — insert()

Never pass a Pydantic model, JSON string, or serialized object to `insert()`. Always pass a plain `dict`:

```python
# Correct
repo.insert(billing_entry.model_dump())

# Wrong — will raise ValueError
repo.insert(billing_entry.model_dump_json())
```

### 6.3 BaseRepository Pattern

All domain repositories inherit from `BaseRepository[T]` in `app/db/repositories/base_repo.py`. This generic class wraps `DatabaseManager` methods and binds them to a specific table and Pydantic model type.

**Constructor signature:** `BaseRepository(manager: DatabaseManager, table_name: str, model_class: Type[T])`

**Adding a new repository:**

```python
# app/db/repositories/billing_entry.py
from app.db.repositories.base_repo import BaseRepository
from app.db.models.billing_entry import BillingEntryInDB
from app.db.supabasemanager import DatabaseManager

class BillingEntryRepository(BaseRepository[BillingEntryInDB]):
    def __init__(self, manager: DatabaseManager):
        super().__init__(manager, "billing_entries", BillingEntryInDB)

    # Add domain-specific methods beyond CRUD here
    def get_unbilled_for_matter(self, matter_id: int) -> list[BillingEntryInDB]:
        return self.select_many(
            condition={"matter_id": matter_id, "billed": False}
        )[0]
```

### 6.4 Model Conventions

Follow the pattern established in `app/db/models/attorney.py`:

- **Domain model** (e.g., `BillingEntry(BaseModel)`) — contains business fields only; no DB metadata
- **DB model** (e.g., `BillingEntryInDB(BillingEntry)`) — extends domain model; adds `id`, `created_at`, `updated_at`; sets `model_config = ConfigDict(from_attributes=True)`
- Every field uses `Field(...)` with a `description=` string

### 6.5 Staff Model Refactor

The `Attorney` model and `AttorneyRepository` in the starter repo are examples only. Replace with:

- `app/db/models/staff.py` → `StaffMember`, `StaffMemberInDB` (see §2.1)
- `app/db/repositories/staff.py` → `StaffRepository(BaseRepository[StaffMemberInDB])`
- `app/db/models/client.py` → `Client`, `ClientInDB`
- `app/db/repositories/client.py` → `ClientRepository`

Delete `app/db/models/attorney.py` and `app/db/repositories/attorney.py` after the refactor is complete and tested.

### 6.6 Key Tables (Summary)

| Table | Purpose |
|-------|---------|
| `staff` | Attorneys, paralegals, and admins |
| `clients` | Client records |
| `matters` | Legal matters; FK to primary client |
| `matter_staff` | Originators + billing reviewer per matter |
| `billing_splits` | Multi-client billing percentage assignments (exception case) |
| `billing_entries` | Time, expense, and flat-fee entries |
| `billing_cycles` | Billing periods with open/closed status |
| `trust_ledger` | Trust account balances and transactions |
| `fee_agreements` | Templates and executed agreements |
| `matter_events` | Deadlines, hearings, appointments |
| `discovery_requests` | Ingested discovery items per matter |
| `discovery_responses` | Client and attorney responses per item |
| `user_roles` | Maps Supabase auth UID to application role |
| `audit_log` | Immutable log of sensitive actions (see §8.5) |
| `inventory_configuration` | (Future) Asset/debt class field definitions |

### 6.7 Migrations

Use **Alembic** for schema migrations. Migration files in `db/migrations/`. Do not modify the Supabase schema directly in Studio in any environment other than a throwaway sandbox.

---

## 7. Runtime Parameters & Environment Configuration

### 7.1 Settings Class

All runtime parameters are managed through the existing `Settings(BaseSettings)` class in `app/util/settings.py`. Conventions to follow:

- snake_case field names (Pydantic maps `SUPABASE_URL` env var → `supabase_url` field automatically)
- Optional fields: `Optional[str] = None`; required fields have no default and raise `ValidationError` on startup if absent
- The module-level singleton `settings = Settings()` is imported throughout — do not instantiate `Settings()` again elsewhere in application code
- Use `settings.getattr(item, default)` for dynamic attribute lookups

**Add these fields** to the existing `Settings` class (do not duplicate fields already present):

```python
# Billing
time_increment_options: list[float] = [0.1, 0.25, 0.5, 1.0]
default_refresh_trigger_pct: float = 0.40

# Stripe
stripe_secret_key: str = ""
stripe_webhook_secret: str = ""
stripe_publishable_key: str = ""  # safe to expose to frontend via GET /api/config
```

The multi-LLM vendor configuration (`llm_vendor`, `anthropic_api_key`, `anthropic_model`, `gemini_api_key`, `gemini_model`, `openai_api_key`, `openai_model`, `groq_api_key`, `groq_model`, `deepseek_api_key`, `deepseek_model`) is already present in `settings.py`. See §9 for LLM service usage.

### 7.2 Environment Files

| File | Purpose | Committed? |
|------|---------|------------|
| `.env` | Local development secrets | ❌ Never |
| `.env.example` | Template with all keys, no values | ✅ Yes |
| `.env.test` | Test environment overrides | ✅ Yes (no real secrets) |
| Docker Compose `environment:` block | Dev overrides | ✅ Yes (dev compose file) |

### 7.3 Frontend Environment Variables

React env vars are prefixed `VITE_` and baked in at build time:

```
VITE_SUPABASE_URL=
VITE_SUPABASE_ANON_KEY=
VITE_API_BASE_URL=http://localhost:8000
VITE_STRIPE_PUBLISHABLE_KEY=
```

`supabase_service_role_key`, all LLM API keys, and `stripe_secret_key` are **never** exposed to the frontend.

---

## 8. Logging Strategy

### 8.1 LoggerFactory Usage

All logging in Cyclone goes through the existing `LoggerFactory` in `app/util/loggerfactory.py`. Declare a module-level logger at the top of every file that needs logging:

```python
from app.util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)
```

Do not call `logging.getLogger()` directly anywhere else in application code.

`LoggerFactory.create_logger(name, loglevel=None)`:
- Reads `settings.log_level` (default: `"WARNING"`) if `loglevel` not passed
- Reads `settings.log_format` for the formatter string
- Clears existing handlers before adding a fresh `StreamHandler`
- Sets `logger.propagate = False` to prevent duplicate log entries
- Falls back to `"INFO"` with a warning if an invalid level string is provided

### 8.2 Log Format

Controlled by `settings.log_format`:

```
%(asctime)s - %(name)-15s - %(levelname)-8s - %(message)s
```

Override in `.env` for production structured logging if needed.

### 8.3 Log Levels by Use Case

| Level | When to Use |
|-------|-------------|
| `DEBUG` | LLM prompt/response text, raw query parameters — dev only |
| `INFO` | Request received, billing entry committed, bill generated, user authenticated |
| `WARNING` | LLM parse failure, Stripe webhook mismatch, conflict check lookup error |
| `ERROR` | Database operation failed, unhandled exception, LLM API call failure |
| `CRITICAL` | System cannot start, missing required config, connection pool exhausted |

### 8.4 No PII in Log Messages

Never log client names, financial amounts, SSNs, or other sensitive data. Reference records by database ID only.

```python
# Correct
LOGGER.info("Billing entry committed: entry_id=%s matter_id=%s", entry_id, matter_id)

# Wrong — never do this
LOGGER.info("Billed .25 hr to Anna Jones for %s", description)
```

### 8.5 Audit Log Table

Sensitive actions are written to an `audit_log` table in Supabase in addition to the application log. This is the permanent, queryable record for compliance.

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

Actions requiring an audit log entry: billing entry created / edited / deleted, bill sent to client, fee agreement signed, billing cycle closed, user role changed, trust ledger transaction posted.

---

## 9. API Integration — LLM & FastAPI

### 9.1 FastAPI Project Structure

```
app/
├── main.py                      # App factory, middleware, router registration
├── util/
│   ├── settings.py              # Existing — Settings(BaseSettings) singleton
│   └── loggerfactory.py         # Existing — LoggerFactory
├── db/
│   ├── supabasemanager.py       # Existing — SupabaseManager(DatabaseManager)
│   ├── models/
│   │   ├── staff.py             # New — replaces attorney.py
│   │   ├── client.py
│   │   ├── matter.py
│   │   ├── billing_entry.py
│   │   └── ...
│   ├── repositories/
│   │   ├── base_repo.py         # Existing — BaseRepository[T]
│   │   ├── staff.py             # New — replaces attorney.py
│   │   ├── client.py
│   │   ├── matter.py
│   │   ├── billing_entry.py
│   │   └── ...
│   └── migrations/
├── routers/
│   ├── auth.py
│   ├── billing.py
│   ├── matters.py
│   ├── clients.py
│   ├── discovery.py
│   └── admin.py
├── services/
│   ├── llm_service.py           # All LLM calls centralized here
│   ├── billing_service.py
│   ├── pdf_service.py
│   └── stripe_service.py
└── dependencies.py              # Shared Depends() — get_db_manager, get_current_user, require_role
```

### 9.2 LLM Service — Multi-Vendor

The existing `Settings` class supports multiple LLM vendors via `settings.llm_vendor`. All LLM calls go through `app/services/llm_service.py` — nothing else in the codebase calls an LLM API directly.

```python
# app/services/llm_service.py
from app.util.settings import settings
from app.util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

class LLMService:
    def __init__(self):
        self.vendor = settings.llm_vendor

    def complete(self, system_prompt: str, user_message: str) -> str:
        """Dispatch to the configured LLM vendor. Returns response text."""
        LOGGER.debug("LLM call: vendor=%s", self.vendor)
        if self.vendor == "anthropic":
            return self._call_anthropic(system_prompt, user_message)
        elif self.vendor == "gemini":
            return self._call_gemini(system_prompt, user_message)
        elif self.vendor == "openai":
            return self._call_openai(system_prompt, user_message)
        elif self.vendor == "groq":
            return self._call_groq(system_prompt, user_message)
        else:
            raise ValueError(f"Unsupported LLM vendor: {self.vendor}")

    def _call_anthropic(self, system_prompt: str, user_message: str) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    # _call_gemini, _call_openai, _call_groq follow the same pattern

llm_service = LLMService()  # module-level singleton
```

### 9.3 Natural Language Billing Entry

**Endpoint:** `POST /api/v1/billing/parse`

**Request body:**
```json
{ "text": "bill .25 to Anna Jones Divorce for drafting initial petition for divorce" }
```

**System prompt** passed to `llm_service.complete()`:

```
You are a legal billing assistant. Parse the user's natural language billing entry
into a structured JSON object with these fields:
- hours (float, using increments: 0.1, 0.25, 0.5, 1.0)
- client_name (string)
- matter_name (string)
- description (string — clean, professional billing language)
- entry_type: "time" | "expense" | "flat_fee"
- billable: true (default) | false

Respond ONLY with valid JSON. No markdown, no explanation.
If a required field cannot be determined, set it to null.
```

**Response to frontend** (preview card — not yet committed to DB):
```json
{
  "parsed": {
    "hours": 0.25,
    "client_name": "Anna Jones",
    "matter_name": "Anna Jones Divorce",
    "description": "Drafted initial petition for divorce",
    "entry_type": "time",
    "billable": true
  },
  "matched_matter_id": 42,
  "confidence": "high"
}
```

Attorney clicks **Commit** to write to DB, or **Edit** to open the form pre-populated with parsed values.

### 9.4 Discovery Request Parsing

**Endpoint:** `POST /api/v1/discovery/ingest`

Accepts pasted text or uploaded document text. LLM segments it into individual discovery requests, classifies each by type (`interrogatory`, `rfa`, `rfp`, `witness_list`), and extracts the request number and source text. Returns an array of structured items for attorney review before database commit.

### 9.5 FastAPI Middleware Stack (in order)

1. `CORSMiddleware` — configured per environment
2. `AuthMiddleware` — validates Supabase JWT, extracts `supabase_uid` and `role` into request state
3. Route handlers with `Depends(require_role([...]))`

### 9.6 API Versioning

All routes prefixed `/api/v1/`. Reserve `/api/` (no version) for utility endpoints: `GET /api/health`, `GET /api/config`.

---

## 10. Docker & Deployment

### 10.1 Services

```yaml
# docker-compose.yml (base / production)
services:
  api:
    build: ./app
    ports:
      - "8000:8000"
    env_file: .env

  frontend:
    build: ./frontend
    ports:
      - "3000:80"
    env_file: .env.frontend
```

### 10.2 Dev Overrides

```yaml
# docker-compose.override.yml (committed — real secrets come from .env, not this file)
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

### 10.3 Startup Checks

On API startup, `main.py`:
1. Pydantic validates all `Settings` fields — raises `ValidationError` on missing required values before the server accepts any requests
2. `SupabaseManager.__init__` verifies `supabase_url` and `supabase_service_role_key` are non-empty; raises `ValueError` otherwise
3. Log startup complete at `INFO` level with `is_development`, `log_level`, and `llm_vendor`

---

## 11. Future Features (Stub Only)

These requirements are acknowledged but deferred. Current architecture must not preclude them.

### 11.1 Trust Accounting / GL Integration
- Cyclone will emit billing and payment events to an external GL system chosen by the firm (Clio, QuickBooks, etc.)
- Pattern: outbox table (`gl_events`) + adapter per GL vendor
- Do not build internal double-entry ledger logic; `trust_ledger` table is sufficient for trust balance tracking in v1

### 11.2 Inventory & Appraisement
- Client, paralegal, and attorney collaboratively build a sworn inventory of community estate assets and debts
- Asset/debt classes and required fields stored in `inventory_configuration` table
- Supports statement upload → LLM extraction OR manual field entry
- Requires a flexible `asset_items` table with `JSONB details` column keyed to `inventory_configuration`

### 11.3 Reporting / Analytics
- Expose Supabase/PostgreSQL to **Metabase** (self-hosted or cloud) rather than building custom reports
- Reserve a read-only `reporting` Postgres role for Metabase access
- No custom report builder in v1

### 11.4 Electronic Signature Integration
- Replace checkbox acknowledgment with DocuSign or HelloSign for fee agreements
- Store signed envelope ID and document back in Supabase Storage, linked to `fee_agreements`

### 11.5 Calendar Integration
- Sync `matter_events` with attorney calendars (Google Calendar, Outlook) via OAuth
- Client portal events view becomes automated rather than manually maintained

---

*End of Cyclone PRD v1.1 — Reflects starter code at https://github.com/tjdaley/cyclone.git*
# Cyclone — Product Requirements Document
**Version:** 2.0  
**Author:** Thomas J. Daley, J.D.  
**Last updated:** 2026-04-06  
**Repo:** https://github.com/tjdaley/cyclone.git  
**Stack:** React (Vite) · FastAPI · Supabase (PostgreSQL) · Docker  
**Status:** Scaffolding complete — backend API, frontend SPA, and database DDL implemented

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [User Roles & Access Control](#2-user-roles--access-control)
3. [Authentication & Staff Correlation](#3-authentication--staff-correlation)
4. [Core Feature Modules](#4-core-feature-modules)
5. [UI Layout & Component Structure](#5-ui-layout--component-structure)
6. [Color Palette & Design Tokens](#6-color-palette--design-tokens)
7. [Database Schema](#7-database-schema)
8. [Runtime Parameters & Environment Configuration](#8-runtime-parameters--environment-configuration)
9. [Logging Strategy](#9-logging-strategy)
10. [API Integration — LLM & FastAPI](#10-api-integration--llm--fastapi)
11. [Docker & Deployment](#11-docker--deployment)
12. [Implementation Status](#12-implementation-status)
13. [Future Features (Stub Only)](#13-future-features-stub-only)

---

## 1. Project Overview

**Cyclone** is a web-based legal practice management platform serving law firms and their clients. It has two distinct interface modes: a **Client Portal** and a **Staff Portal** (attorneys, paralegals, admins). Both share the same backend but present dramatically different UX densities and workflows.

### Core Goals
- Streamline client onboarding, conflict checking, and fee agreement execution
- Provide attorneys and paralegals with fast, flexible billing entry — including a natural-language/free-text billing interface powered by an LLM
- Expose billing statements to clients via a portal with integrated Stripe payment processing
- Support multi-client billing splits for appointed matters (discovery master, mediator, etc.)
- Support per-matter rate overrides and pro bono matters
- Provide a discovery response workflow where clients contribute directly to interrogatory responses, witness lists, RFAs, and RFPs
- Run entirely inside Docker with dev/prod environment overrides

### What Cyclone Is Not (Yet)
- Not a general ledger or trust accounting system (GL integration is a future stub)
- Not a full inventory & appraisement tool (future feature — architecture must accommodate it)
- Not a case management / docket system (events/deadlines are read-only views from manual entry or future calendar integration)

---

## 2. User Roles & Access Control

Authentication is handled by **Supabase Auth**. Staff log in with Google OAuth; their identity is correlated with a pre-existing staff record on first login (see §3).

### 2.1 Staff Model

The unified `StaffMember` model (`app/db/models/staff.py`) covers attorneys, paralegals, and admins:

- `role`: `StaffRole` enum — `attorney`, `paralegal`, `admin`
- `name`: `FullName` (courtesy_title, first_name, middle_name, last_name, suffix)
- `bar_admissions`: list of `BarAdmission` (bar_number, state) — empty for non-attorneys
- `auth_email`: email address for first-login correlation (set by admin before the person signs in)
- `supabase_uid`: set automatically on first login via the correlation flow — `None` until then
- `default_billing_rate`: optional hourly rate in USD — `None` for admins who don't bill time
- `slug`: URL-safe unique identifier for routing

### 2.2 Role Table

The `user_roles` table maps a Supabase `auth.users` UID to an application role:

| `supabase_uid` | `role` | `staff_id` | `client_id` |
|----------------|--------|------------|-------------|
| uuid | `attorney` | FK → staff | null |
| uuid | `client` | null | FK → clients |

### 2.3 Role Enforcement

- **Middleware layer:** `AuthMiddleware` validates the Supabase JWT and injects `supabase_uid`, `role` (JWT claim), and `email` into `request.state`
- **Route layer:** `require_role(["attorney", "admin"])` (via `Depends()`) resolves the **authoritative** role from the `user_roles` table — not the JWT claim — preventing stale-JWT privilege escalation
- **Frontend:** React conditionally renders nav items and portal mode based on the role in `AuthContext`
- **Database backstop:** Supabase RLS policies mirror role logic (see `db/migrations/005_rls.sql`)

### 2.4 Role Summary

| Role | Portal | Description |
|------|--------|-------------|
| `client` | Client Portal | Sees only their own matters, bills, events, and discovery tasks |
| `attorney` | Staff Portal | Full billing, matter management, and discovery access |
| `paralegal` | Staff Portal | Same as attorney except cannot approve bills or sign fee agreements |
| `admin` | Staff Portal | Full system access: settings, user management, fee templates, reports |

---

## 3. Authentication & Staff Correlation

### The Problem
Staff members exist in the system before they have a Supabase Auth account. Admin creates staff records with work details, but there's no `supabase_uid` until the person signs in for the first time.

### The Solution — Auth Email Correlation Flow

1. **Admin pre-populates `auth_email`** on the staff record with the email address the person will use to sign in (e.g. their Google account email)
2. **First login via Google OAuth** routes through `/auth/callback` → `/onboarding`
3. **Onboarding page calls `POST /api/v1/auth/correlate-staff`** which:
   - Reads the authenticated user's email from `request.state.email`
   - Queries `staff` for a record where `auth_email` matches AND `supabase_uid IS NULL`
   - If found: writes the Supabase Auth UID into `staff.supabase_uid` and creates a `user_roles` record
   - If not found: returns 404 (user sees "No firm account found — contact your administrator")
4. **Subsequent logins** skip onboarding entirely — `GET /api/v1/auth/me` finds the existing role record and routes directly to the dashboard

### Key Implementation Details
- `auth_email` has a UNIQUE constraint and a partial index (`WHERE supabase_uid IS NULL`) for efficient first-login queries
- The correlation endpoint is idempotent — calling it again after linking returns the existing role
- `staff.supabase_uid` was made nullable in migration `006_staff_auth_fields.sql`
- Neither `/api/v1/auth/me` nor `/api/v1/auth/correlate-staff` requires `require_role()` — they run for any authenticated user

---

## 4. Core Feature Modules

### 4.1 Client Onboarding (Client-Facing)

**4.1.1 Intake Form**
- Multi-step form: contact info, matter type, opposing party, children (if applicable), prior counsel, referral source
- Form state persisted in Supabase as a draft until submitted; allows resume on return visit
- On submission, triggers a backend task to run the conflict check and notify the responsible attorney

**4.1.2 Conflict Check**
- Queries `matters`, `clients`, and `opposing_parties` for name matches
- Phase 1 (implemented): Python substring match in `ConflictService`
- Phase 2 (architecture ready): fuzzy match via `pg_trgm` using `search_conflicts()` stored function in `db/migrations/004_functions.sql`
- Results surfaced to an attorney or admin for review before the matter is activated
- Client sees a "pending review" status; conflict details are not disclosed to the client

**4.1.3 Fee Agreement**
- Attorney configures a fee agreement template per matter type in the admin UI
- Template pre-populated with client name, matter type, retainer amount, refresh trigger percentage (default: 40%), and hourly rates
- Client reviews and executes via electronic signature (Phase 1: checkbox acknowledgment + timestamp; Phase 2: DocuSign or similar)
- Executed agreement stored as a PDF snapshot in Supabase Storage, linked to the matter record

---

### 4.2 Client Portal (Client-Facing)

**4.2.1 Billing Review**
- Available after billing cycle status = `closed`
- Itemized bill: date, timekeeper, description, hours/units, rate, amount
- Stripe-hosted payment link embedded in portal for direct payment
- PDF bill downloadable from portal

**4.2.2 Events & Deadlines**
- Read-only ascending list of upcoming hearings, deadlines, and appointments
- Past events hidden by default; toggle to show
- Data source: `matter_events` table (manual entry; future: calendar sync)

---

### 4.3 Staff Portal — Billing

**4.3.1 Billing Entry — Natural Language Interface**
- Free-text field, e.g.: `"bill .25 to Anna Jones Divorce for drafting initial petition for divorce"`
- Input sent to the configured LLM via `POST /api/v1/billing/parse`; structured billing entry returned for attorney review
- Attorney reviews parsed entry in a confirmation card → **Commit** writes to DB; **Edit** pre-populates the form
- Parse failure returns a validation message with a suggestion to clarify

**4.3.2 Billing Entry — Form Interface**
- Autocomplete selectors: Client → Matter → Timekeeper
- Entry type: `time` | `expense` | `flat_fee`
  - **Time:** Duration in increments defined in `Settings.time_increment_options` (e.g., `[0.1, 0.25, 0.5, 1.0]`); optional non-billable flag; rate from matter rate card (overridable); optional fixed-amount override
  - **Expense:** Amount, vendor/description, optional receipt upload
  - **Flat fee:** Fixed dollar amount, description; hours field hidden
- **Client Financial Balance Widget** shown on form:
  - Formula: `trust_balance − sum(unbilled time entries) − sum(unbilled expense entries)`
  - Red: balance < $0 / Yellow: balance < (retainer × refresh_trigger_pct) / Green: otherwise
  - Refreshes on client/matter selection and after each committed entry

**4.3.3 Rate Resolution**

Billing rate for a TIME entry is resolved in this order (implemented in `BillingService.resolve_rate()`):

1. `matter_rate_overrides` — per-staff, per-matter override
2. `matter.rate_card` — rates by role (e.g. `{"attorney": 350, "paralegal": 150}`)
3. `staff.default_billing_rate` — staff member's default rate
4. **Pro bono override:** if `matter.is_pro_bono` is True, TIME entries always get rate=0, amount=0

This logic exists in both:
- Python: `BillingService.resolve_rate()` in `app/services/billing_service.py`
- SQL: `resolve_billing_rate()` function in `db/migrations/004_functions.sql` (for reporting queries)
- DB trigger: `enforce_pro_bono_zero_rate` in `db/migrations/003_indexes_triggers.sql` (backstop)

**4.3.4 Billing Review & Edit**
- Dual views: **By Timekeeper** and **By Matter**
- Inline editing of description, duration, rate, billable flag
- Guard: billed entries cannot be edited — `prevent_billed_entry_edit` DB trigger as backstop
- Filters: date range, entry type, billable/non-billable, billing cycle status

**4.3.5 Bill Production**
- Attorney or admin triggers bill generation for a matter + billing cycle
- Server compiles unbilled entries, applies billing splits, renders PDF (WeasyPrint), stores in Supabase Storage
- Bill emailed to client(s) and made available in the client portal automatically
- Stripe payment link appended to emailed and portal-hosted PDF

---

### 4.4 Matter Configuration (Attorney/Admin)

Each matter contains:
- `matter_name`, `matter_type` (enum: divorce, child_custody, modification, enforcement, cps, probate, estate_planning, civil, other), `status` (intake → conflict_review → active → closed → archived)
- `billing_review_staff_id`: single attorney responsible for bill approval
- `rate_card`: JSONB — hourly rates by staff role or individual staff member
- `retainer_amount`, `refresh_trigger_pct` (default: 0.40, expressed as a fraction 0–1)
- `is_pro_bono`: when True, all TIME entries are billed at $0.00

**Billing Splits**: `billing_splits` table holds `{matter_id, client_id, split_pct}` — must sum to 100%. DB constraint trigger `validate_billing_splits_sum` enforces this.

**Originating Attorneys**: `matter_staff` table with `split_pct` that must sum to 100% across all originating records. DB constraint trigger `validate_originating_split_sum` enforces this.

---

### 4.5 Discovery Response Module (Client + Attorney/Paralegal)

**4.5.1 Ingestion**
- Attorney or paralegal uploads or pastes opposing counsel's discovery requests
- LLM parses and segments into individual `discovery_requests` records: type (`interrogatory`, `rfa`, `rfp`, `witness_list`), request number, source text
- Endpoint: `POST /api/v1/discovery/ingest`

**4.5.2 Client Tasks**
- **Witness Lists:** Add witnesses (name, address, relationship, expected testimony)
- **Interrogatories:** Read each; type draft response
- **RFAs:** Read each; select Admit / Deny / Lack Sufficient Information; optional explanatory note (`RFASelection` enum)
- **RFPs:** Indicate whether responsive documents may exist; optionally upload documents

**4.5.3 Attorney/Paralegal Review**
- Per item: enter objection, assert privilege, add interpretive note, edit client response, mark as final
- Status flow: `pending_client` → `client_responded` → `attorney_review` → `finalized` | `objected`
- Final responses exportable to formatted Word or PDF for service

---

### 4.6 Trust Ledger

- Append-only transaction log: deposits (positive), withdrawals (negative), refunds (negative)
- DB trigger `deny_trust_ledger_mutation` blocks UPDATE and DELETE — entries are immutable
- Running balance derived by summing all entries for a matter
- Balance endpoint: `GET /api/v1/billing/balance/{matter_id}` returns trust_balance, unbilled_total, net balance, and status indicator (green/yellow/red)

### 4.7 Audit Log

- Append-only: DB trigger `deny_audit_log_mutation` blocks UPDATE and DELETE
- `AuditLogger` service inserts records — never re-raises on failure (prevents audit errors from crashing primary operations)
- Actions logged: billing entry CRUD, billing cycle closed, bill sent, fee agreement signed, trust ledger transaction, user role changed

---

## 5. UI Layout & Component Structure

### 5.1 Dual-Density Design

| Mode | Roles | Characteristics |
|------|-------|-----------------|
| **Relaxed** (Client Portal) | `client` | Generous padding, larger type, single-column on mobile, minimal chrome, guided step-by-step flows |
| **Compact** (Staff Portal) | `attorney`, `paralegal`, `admin` | Tighter grid, data-dense tables, sidebar nav, keyboard-friendly, more info per viewport |

Layout mode set by `AuthContext` via `document.body.dataset.density` based on authenticated role.

### 5.2 Implemented Component Hierarchy

```
App
├── AuthProvider (Supabase session context, density management)
├── BrowserRouter
│   ├── Public routes
│   │   ├── / → LandingPage (marketing, hero, features, CTA)
│   │   ├── /login → LoginPage (Google OAuth sign-in)
│   │   ├── /auth/callback → AuthCallbackPage (session exchange, routing)
│   │   ├── /onboarding → OnboardingPage (staff correlation)
│   │   └── /access-denied → AccessDeniedPage
│   │
│   └── /app/* → ProtectedRoute
│       └── AppShell (sidebar nav, mobile hamburger)
│           ├── /app/dashboard → DashboardPage (stats, recent matters)
│           ├── /app/billing → BillingPage (NL entry, matter entries)
│           ├── /app/matters → MattersPage (filterable list)
│           ├── /app/clients → ClientsPage (conflict check + list)
│           ├── /app/discovery → DiscoveryPage (requests by matter)
│           └── /app/admin → AdminPage (staff management, admin-only)
```

### 5.3 Responsive Breakpoints

| Breakpoint | Width | Behavior |
|------------|-------|----------|
| `xs` | < 480px | Single column; staff nav = hamburger |
| `sm` | 480–768px | Two-column forms; sidebar hidden, top nav |
| `md` | 768–1024px | Sidebar visible, icon-only (collapsed) |
| `lg` | 1024px+ | Full sidebar with labels; all table columns visible |

CSS Grid + `min-width` media queries. Tailwind CSS utility classes throughout.

### 5.4 Key Shared Components (To Build)

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

## 6. Color Palette & Design Tokens

### 6.1 Brand Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `navy` | `#003057` | Headers, sidebar background, primary buttons |
| `navy-light` | `#004a8f` | Hover on navy elements |
| `gold` | `#C9A84C` | Active nav, highlights, CTA borders |
| `gold-light` | `#e8c97a` | Hover on gold elements |
| `white` | `#FFFFFF` | Page backgrounds (client portal), card backgrounds |
| `off-white` | `#F5F5F0` | Staff portal background; reduces eye strain on dense UIs |

### 6.2 Semantic Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `success` | `#2D7A4F` | Green balance, positive confirmations |
| `warning` | `#B87E00` | Yellow/amber balance, caution states |
| `danger` | `#C0392B` | Red balance, errors, destructive actions |
| `text-primary` | `#1A1A1A` | Body text |
| `text-secondary` | `#5A5A5A` | Labels, helper text |
| `border` | `#D4D4CF` | Table borders, input outlines, dividers |

### 6.3 Typography

| Role | Font | Weight | Size (desktop) |
|------|------|--------|----------------|
| Display / Page Title | `Playfair Display` | 700 | 28–32px |
| Section Heading | `Inter` | 600 | 18–22px |
| Body | `Inter` | 400 | 15px (client) / 14px (staff) |
| Label / Caption | `Inter` | 500 | 12px |
| Monospace (amounts, IDs) | `JetBrains Mono` | 400 | 13px |

Google Fonts loaded in `frontend/index.html`. Font families mapped in `tailwind.config.js` as `font-display`, `font-sans`, and `font-mono`.

---

## 7. Database Schema

### 7.1 Migration Files

All DDL lives in `db/migrations/`:

| File | Purpose |
|------|---------|
| `001_extensions.sql` | Enable `pg_trgm` and `pgcrypto` extensions |
| `002_tables.sql` | 17 tables: offices → staff → clients → matters → billing → trust → discovery → audit |
| `003_indexes_triggers.sql` | B-tree and GIN indexes; triggers for `updated_at`, immutability guards, pro bono enforcement, billing split validation |
| `004_functions.sql` | `search_conflicts()`, `resolve_billing_rate()`, `get_matter_balance()`; views: `v_matter_summary`, `v_unbilled_entries`, `v_discovery_progress` |
| `005_rls.sql` | Row-Level Security policies for all tables; `auth_role()`, `auth_client_id()`, `auth_staff_id()` SECURITY DEFINER helpers |
| `006_staff_auth_fields.sql` | Makes `supabase_uid` nullable, adds `auth_email` UNIQUE, partial index for correlation |
| `run_all.sql` | Master script that runs all migrations in order |

### 7.2 Key Tables

| Table | Purpose | Immutable? |
|-------|---------|------------|
| `offices` | Firm office locations | No |
| `staff` | Attorneys, paralegals, admins | No |
| `clients` | Client records | No |
| `matters` | Legal matters; FK to primary client | No |
| `matter_staff` | Originators + billing reviewer per matter | No |
| `matter_rate_overrides` | Per-staff, per-matter hourly rate overrides | No |
| `billing_splits` | Multi-client billing percentage assignments | No |
| `opposing_parties` | Named parties for conflict checking | No |
| `billing_entries` | Time, expense, and flat-fee line items | Billed entries locked by trigger |
| `billing_cycles` | Billing periods with open/closed status | Closed cycles locked |
| `trust_ledger` | Append-only trust account transactions | Yes — UPDATE/DELETE blocked by trigger |
| `fee_agreements` | Templates and executed agreements | No |
| `matter_events` | Deadlines, hearings, appointments | No |
| `discovery_requests` | Ingested discovery items per matter | No |
| `discovery_responses` | Client and attorney responses per item | No |
| `user_roles` | Maps Supabase auth UID to application role | No |
| `audit_log` | Immutable log of sensitive actions | Yes — UPDATE/DELETE blocked by trigger |

### 7.3 Key Triggers

| Trigger | Table | Purpose |
|---------|-------|---------|
| `set_updated_at` | 12 tables | Auto-set `updated_at` on UPDATE |
| `deny_trust_ledger_mutation` | `trust_ledger` | Block UPDATE/DELETE |
| `deny_audit_log_mutation` | `audit_log` | Block UPDATE/DELETE |
| `enforce_pro_bono_zero_rate` | `billing_entries` | Force rate=0, amount=0 for TIME entries on pro bono matters |
| `prevent_billed_entry_edit` | `billing_entries` | Block changes to entries where billed=true |
| `validate_billing_splits_sum` | `billing_splits` | Ensure splits sum to 100% per matter (DEFERRABLE) |
| `validate_originating_split_sum` | `matter_staff` | Ensure originating splits sum to 100% per matter |

### 7.4 Key Functions

| Function | Purpose |
|----------|---------|
| `search_conflicts(query, threshold)` | Trigram similarity search across clients and opposing parties |
| `search_conflicts_multi(names[], threshold)` | Multi-name conflict search |
| `resolve_billing_rate(p_matter_id, p_staff_id)` | SQL rate resolution (override → rate_card → default) |
| `get_matter_balance(p_matter_id)` | Trust balance minus unbilled entries |

### 7.5 Key Views

| View | Purpose |
|------|---------|
| `v_matter_summary` | Matter with client name, staff count, entry count |
| `v_unbilled_entries` | All unbilled entries with staff and matter names |
| `v_discovery_progress` | Per-matter discovery response completion stats |

---

## 8. Runtime Parameters & Environment Configuration

### 8.1 Settings Class (`app/util/settings.py`)

All runtime parameters managed through `Settings(BaseSettings)`. Module-level singleton: `settings = Settings()`.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `version` | `str` | `"2026.04.05"` | API version string |
| `host_url` | `str` | `"http://localhost:8000"` | CORS and URL generation |
| `is_development` | `bool` | `False` | Gates debug behavior, docs endpoints |
| `firm_name` | `str` | `"Your Law Firm"` | Displayed in config endpoint |
| `db_type` | `str` | `"supabase"` | Database provider |
| `supabase_url` | `str` | `""` | Supabase project URL |
| `supabase_service_role_key` | `str` | `""` | Backend-only; bypasses RLS |
| `supabase_jwt_secret` | `str` | `""` | For JWT validation in auth middleware |
| `supabase_anon_key` | `str` | `""` | Used by frontend Supabase JS client |
| `llm_vendor` | `str` | `"gemini"` | Active LLM vendor |
| `llm_fast_vendor` | `str` | `"gemini"` | Vendor for latency-sensitive calls |
| `llm_temperature` | `float` | `0.1` | LLM temperature |
| `llm_top_p` | `float` | `0.1` | LLM top-p sampling |
| `time_increment_options` | `list` | `[0.1, 0.25, 0.5, 1.0]` | Valid billing time increments |
| `default_refresh_trigger_pct` | `float` | `0.40` | Default retainer refresh threshold |
| `stripe_secret_key` | `str` | `""` | Stripe server-side key |
| `stripe_webhook_secret` | `str` | `""` | Webhook validation |
| `stripe_publishable_key` | `str` | `""` | Safe to expose via `/api/config` |
| `log_level` | `str` | `"WARNING"` | Python logging level |
| `log_format` | `str` | format string | Python logging format |

LLM vendor fields: `{vendor}_api_key`, `{vendor}_model`, and optionally `{vendor}_base_url` for each of: `gemini`, `openai`, `anthropic`, `groq`, `deepseek`.

### 8.2 Environment Files

| File | Purpose | Committed? |
|------|---------|------------|
| `.env` | Local development secrets | Never |
| `.env.example` | Template with all keys, no values | Yes |
| `.env.frontend.example` | Frontend-only env var template | Yes |
| `docker-compose.override.yml` | Dev overrides | Yes (no secrets) |

### 8.3 Frontend Environment Variables

React env vars prefixed `VITE_` and baked in at build time:

| Variable | Purpose |
|----------|---------|
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Subject to RLS |
| `VITE_API_BASE_URL` | FastAPI backend URL (proxied in dev via Vite) |

**Never expose to the frontend:** `SUPABASE_SERVICE_ROLE_KEY`, all LLM API keys, `STRIPE_SECRET_KEY`.

---

## 9. Logging Strategy

### 9.1 LoggerFactory

All logging goes through `LoggerFactory.create_logger(__name__)` in `app/util/loggerfactory.py`. Never call `logging.getLogger()` directly.

### 9.2 Log Levels

| Level | When to Use |
|-------|-------------|
| `DEBUG` | LLM prompt/response text, raw query parameters — dev only |
| `INFO` | Request received, billing entry committed, bill generated, user authenticated |
| `WARNING` | LLM parse failure, Stripe webhook mismatch, conflict check lookup error |
| `ERROR` | Database operation failed, unhandled exception, LLM API call failure |
| `CRITICAL` | System cannot start, missing required config, connection pool exhausted |

### 9.3 Rules
- Use `%s` format args in all log calls — never f-strings (lazy evaluation)
- **No PII in log messages.** Never log client names, financial amounts, SSNs, or case facts. Reference records by database ID only
- `supabasemanager.py` pins `httpx` and `postgrest` to `DEBUG` at module load — do not override elsewhere

---

## 10. API Integration — LLM & FastAPI

### 10.1 FastAPI Route Map

**Public (no auth):**
| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/config` | Public config (Stripe key, firm name, time increments, version) |

**Authenticated (no role required):**
| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/auth/me` | Current user's role profile or null |
| `POST` | `/api/v1/auth/correlate-staff` | First-login staff linking |

**Staff routes (attorney | paralegal | admin):**
| Method | Path | Purpose |
|--------|------|---------|
| `GET/POST` | `/api/v1/staff` | List / create staff |
| `GET/PATCH/DELETE` | `/api/v1/staff/{id}` | Read / update / delete staff |
| `GET/POST` | `/api/v1/clients` | List / create clients |
| `GET/PATCH` | `/api/v1/clients/{id}` | Read / update client |
| `POST` | `/api/v1/clients/conflict-check` | Conflict of interest search |
| `GET/POST` | `/api/v1/matters` | List / create matters |
| `GET/PATCH` | `/api/v1/matters/{id}` | Read / update matter |
| `GET/POST` | `/api/v1/billing/entries` | List / create billing entries |
| `PATCH/DELETE` | `/api/v1/billing/entries/{id}` | Update / delete entry |
| `GET` | `/api/v1/billing/balance/{matter_id}` | Client financial balance |
| `POST` | `/api/v1/billing/parse` | Natural language billing parse |
| `GET/POST` | `/api/v1/billing/cycles` | List / create billing cycles |
| `POST` | `/api/v1/billing/cycles/{id}/close` | Close billing cycle |
| `GET` | `/api/v1/discovery/{matter_id}/requests` | List discovery requests |
| `POST` | `/api/v1/discovery/ingest` | LLM-powered discovery parsing |

**Admin-only routes:**
| Method | Path | Purpose |
|--------|------|---------|
| `GET/POST` | `/api/v1/admin/user-roles` | List / assign roles |
| `PATCH` | `/api/v1/admin/user-roles/{id}` | Update role |
| `GET` | `/api/v1/admin/audit-log` | Query audit log |

### 10.2 LLM Service

All LLM calls go through `LLMService` singleton in `app/services/llm_service.py`. No other file imports an LLM SDK directly.

- `complete(system_prompt, user_message)` → dispatches to `settings.llm_vendor`
- `complete_fast(system_prompt, user_message)` → dispatches to `settings.llm_fast_vendor`
- Supported vendors: `anthropic`, `gemini`, `openai`, `groq`, `deepseek`
- Lazy imports per vendor to avoid loading unused SDKs
- Always log at DEBUG before/after LLM calls

### 10.3 Middleware Stack (in order)

1. `CORSMiddleware` — configured per environment (localhost in dev, `host_url` in prod)
2. `AuthMiddleware` — validates Supabase JWT; injects `supabase_uid`, `role`, `email` into `request.state`; excluded paths: `/api/health`, `/api/config`, `/docs`, `/openapi.json`, `/redoc`
3. Route-level `Depends(require_role([...]))` — authoritative role check against `user_roles` table

---

## 11. Docker & Deployment

### 11.1 Services

```yaml
# docker-compose.yml (production)
services:
  api:
    build: ./app          # Python 3.11-slim + requirements.txt
    ports: ["8000:8000"]
    env_file: .env

  frontend:
    build: ./frontend     # Multi-stage: node 20 build → nginx serve
    ports: ["3000:80"]
    env_file: .env.frontend
    depends_on: [api]
```

```yaml
# docker-compose.override.yml (dev — auto-applied)
services:
  api:
    volumes: [./app:/app]
    environment: [IS_DEVELOPMENT=true, LOG_LEVEL=DEBUG]
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    volumes: [./frontend:/app, /app/node_modules]
    environment: [VITE_API_BASE_URL=http://localhost:8000]
```

### 11.2 Running Locally Without Docker

```bash
# Terminal 1 — backend
uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

The Vite dev server (port 3000) proxies `/api` requests to `http://localhost:8000` via `vite.config.ts`.

### 11.3 Production Frontend

The frontend Dockerfile runs a multi-stage build: `npm run build` produces a static bundle in `dist/`, which is served by nginx. The nginx config (`frontend/nginx.conf`) handles SPA fallback (`try_files $uri $uri/ /index.html`) and proxies `/api/` requests to the `api` service.

### 11.4 Startup Checks

1. Pydantic validates all `Settings` fields — raises `ValidationError` on missing required values
2. `SupabaseManager.__init__` verifies credentials are non-empty
3. `_lifespan` logs: `"Cyclone API started | env=%s llm_vendor=%s log_level=%s"`

---

## 12. Implementation Status

### Fully Implemented
- All 12 Pydantic models with domain + InDB variants
- All 14 repository classes with domain-specific query methods
- `SupabaseManager` with retry logic, error handling
- `AuthMiddleware` with JWT validation and email extraction
- `require_role()` with authoritative DB-based role check
- `LLMService` with 5-vendor dispatch and fast/standard modes
- `BillingService` with rate resolution, pro bono enforcement, NL parsing
- `ConflictService` with Phase 1 substring matching
- `AuditLogger` with fail-safe logging
- 8 FastAPI routers covering all API endpoints
- Request/response schemas for all endpoints
- 6 SQL migration files with tables, indexes, triggers, functions, RLS, and views
- Full React frontend: landing page, auth flow, 6 app pages, sidebar nav
- Docker Compose with production and dev override configurations
- Dockerfiles for both API and frontend

### Not Yet Implemented
- Client Portal (separate from staff portal — currently only staff portal pages exist)
- PDF bill generation (WeasyPrint)
- Stripe payment integration (keys configured but no webhook handler or checkout flow)
- Email notification service
- Fee agreement template CRUD and electronic signature
- Discovery response editing UI (request viewing is implemented; response workflow is not)
- Intake form / StepWizard onboarding for new clients
- File upload (receipts, discovery documents)
- Reusable shared components (`DataTable`, `StatusBadge`, `ClientBalanceWidget`, `ConfirmDialog`)
- Test suite (unit and integration tests)
- Phase 2 conflict checking (pg_trgm RPC — SQL functions exist, Python wiring is substring only)

---

## 13. Future Features (Stub Only)

These requirements are acknowledged but deferred. Current architecture must not preclude them.

### 13.1 Trust Accounting / GL Integration
- Cyclone will emit billing and payment events to an external GL system (Clio, QuickBooks, etc.)
- Pattern: outbox table (`gl_events`) + adapter per GL vendor
- Do not build internal double-entry ledger logic; `trust_ledger` table is sufficient for trust balance tracking in v1

### 13.2 Inventory & Appraisement
- Client, paralegal, and attorney collaboratively build a sworn inventory of community estate assets and debts
- Asset/debt classes and required fields stored in `inventory_configuration` table
- Supports statement upload → LLM extraction OR manual field entry
- Requires a flexible `asset_items` table with `JSONB details` column keyed to `inventory_configuration`

### 13.3 Reporting / Analytics
- Expose PostgreSQL to **Metabase** rather than building custom reports
- Reserve a read-only `reporting` Postgres role for Metabase access

### 13.4 Electronic Signature Integration
- Replace checkbox acknowledgment with DocuSign or HelloSign for fee agreements

### 13.5 Calendar Integration
- Sync `matter_events` with attorney calendars (Google Calendar, Outlook) via OAuth

---

*End of Cyclone PRD v2.0*

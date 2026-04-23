# Cyclone — Product Requirements Document

**Version:** 3.0
**Author:** Thomas J. Daley, J.D.
**Last updated:** 2026-04-13
**Repo:** <https://github.com/tjdaley/cyclone.git>
**Stack:** React (Vite) · FastAPI · Supabase (PostgreSQL + Storage) · Docker
**Status:** Active development — staff portal features substantially built; client portal, Stripe, and PDF bill generation deferred.

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
10. [API Integration — LLM, PDF, Storage, FastAPI](#10-api-integration--llm-pdf-storage-fastapi)
11. [Docker & Deployment](#11-docker--deployment)
12. [Implementation Status](#12-implementation-status)
13. [Future Features (Stub Only)](#13-future-features-stub-only)

---

## 1. Project Overview

**Cyclone** is a web-based legal practice management platform serving law firms and their clients. It has two distinct interface modes: a **Client Portal** and a **Staff Portal** (attorneys, paralegals, admins). Both share the same backend but present dramatically different UX densities and workflows.

### Core Goals

- Streamline client intake, conflict checking, and fee agreement execution
- Provide attorneys and paralegals with fast, flexible billing entry — including a natural-language/free-text billing interface powered by an LLM
- **Ingest opposing counsel's discovery requests** from PDFs, extract each numbered request verbatim, and provide a full editing UI for interpretations, privileges, objections, and responses
- **Ingest pleadings** to extract case metadata (court, county, matter number), children, opposing counsel, and each party's claims and defenses. These claims guide discovery drafting, objections, and witness examination
- **Deduplicate opposing counsel** across matters by bar number so contact updates propagate automatically
- Export finalized discovery responses to Word for paralegal review and filing
- Expose billing statements to clients via a portal with integrated Stripe payment processing (future)
- Support multi-client billing splits for appointed matters
- Support per-matter rate overrides and pro bono matters
- Run entirely inside Docker with dev/prod environment overrides

### What Cyclone Is Not (Yet)

- Not a general ledger or trust accounting system (GL integration is a future stub)
- Not a full inventory & appraisement tool (future feature — architecture must accommodate it)
- Not a case management / docket system (events/deadlines are read-only views from manual entry or future calendar integration)

---

## 2. User Roles & Access Control

Authentication is handled by **Supabase Auth**. Staff log in with Google OAuth; their identity is correlated with a pre-existing `user_roles` record on first login (see §3).

### 2.1 Staff Model

The unified `StaffMember` model (`app/db/models/staff.py`) covers attorneys, paralegals, and admins:

- `role`: `StaffRole` enum — `attorney`, `paralegal`, `admin`
- `name`: `FullName` (courtesy_title, first_name, middle_name, last_name, suffix) — the canonical name shape used across the codebase for all people
- `bar_admissions`: list of `BarAdmission` (bar_number, state) — empty for non-attorneys
- `auth_email`: email address for first-login correlation (set by admin before the person signs in)
- `supabase_uid`: nullable; set automatically on first login via the correlation flow
- `default_billing_rate`: optional hourly rate in USD — `None` for admins who don't bill time
- `slug`: URL-safe unique identifier

### 2.2 Auth Entry Point

The `user_roles` table is the **auth entry point**. Every authenticated request resolves to a single row here:

| Column | Notes |
|--------|-------|
| `supabase_uid` | nullable; matches `auth.users.id`. Populated during correlation. |
| `auth_email` | nullable; used for first-login matching. |
| `role` | `attorney` \| `paralegal` \| `admin` \| `client` |
| `staff_id` | FK → `staff` (for staff roles) |
| `client_id` | FK → `clients` (for client role) |

Lookup on every request: `user_roles WHERE supabase_uid = <jwt sub>` — single query, no joins.

### 2.3 Role Enforcement

- **Middleware layer:** `AuthMiddleware` validates the Supabase JWT via JWKS/ES256 and injects `supabase_uid`, `role` (JWT claim), and `email` into `request.state`
- **Route layer:** `require_role(["attorney", "admin"])` (via `Depends()`) resolves the **authoritative** role from the `user_roles` table — not the JWT claim — preventing stale-JWT privilege escalation
- **Frontend:** React conditionally renders nav items and portal mode based on the role in `AuthContext`
- **Database backstop:** Supabase RLS policies exist but are bypassed by the service role key used by the backend; RBAC is enforced at the FastAPI layer

### 2.4 Role Summary

| Role | Portal | Description |
|------|--------|-------------|
| `client` | Client Portal | Will see only their own matters, bills, events, and discovery tasks. **Not yet implemented** — currently redirects to `/access-denied` |
| `attorney` | Staff Portal | Full billing, matter management, discovery, and pleading access |
| `paralegal` | Staff Portal | Same as attorney except cannot create matters or approve bills |
| `admin` | Staff Portal | Full system access: staff management, rate overrides, user roles |

---

## 3. Authentication & Staff Correlation

### The Problem

Staff members exist in the system before they have a Supabase Auth account. Admin creates staff records with work details, but there's no `supabase_uid` until the person signs in for the first time.

### The Solution — Auth Email Correlation Flow

1. **Admin pre-populates `auth_email`** on both a `staff` record and a matching `user_roles` record with the email address the person will use to sign in (their Google account email)
2. **First login via Google OAuth** routes through `/auth/callback` → `/onboarding`
3. **Onboarding page calls `POST /api/v1/auth/correlate-staff`** which:
   - Reads the authenticated user's email from `request.state.email`
   - Queries `user_roles` for a record where `auth_email` matches AND `supabase_uid IS NULL`
   - If found: writes the Supabase Auth UID into both `user_roles.supabase_uid` and the linked `staff.supabase_uid`
   - If not found: returns 404 (user sees "No firm account found — contact your administrator")
4. **Subsequent logins** skip onboarding entirely — `GET /api/v1/auth/me` finds the existing role record via `supabase_uid` and routes directly to the dashboard

### Key Implementation Details

- `user_roles` is the single auth entry point — no two-hop lookup
- The correlation endpoint is idempotent — calling it again after linking returns the existing role
- Neither `/api/v1/auth/me` nor `/api/v1/auth/correlate-staff` requires `require_role()` — they run for any authenticated user
- `GET /api/v1/auth/me` returns **404** (not null) when no role is found. The frontend treats 404 as "needs correlation"

---

## 4. Core Feature Modules

### 4.1 Client Management

**4.1.1 Client Intake**
- Attorneys, paralegals, and admins can create clients with: name (FullName), auth_email (for future portal login), contact email, telephone, referral type (from `settings.referral_types`), referral source, referred-to staff, prior counsel, OK-to-rehire flag, ending A/R balance
- Clients have a `status` lifecycle: `prospect` → `pending_conflict_check` → `conflict_flagged` → `active` → `inactive`

**4.1.2 Conflict Check**
- Attorney runs a name search against `clients`, `matters`, and `opposing_parties`
- Phase 1 (implemented): Python substring match in `ConflictService`
- Phase 2 (architecture ready): fuzzy match via `pg_trgm` using `search_conflicts()` stored function
- Results surfaced to the attorney for review; conflict details never disclosed to the prospective client

**4.1.3 Fee Agreement** *(Not yet built)*
- Template per matter type; client executes electronically; PDF stored in Supabase Storage

---

### 4.2 Matter Management

Each matter contains:

- `matter_name`, `short_name` (auto-generated as `LASTNAME - matter type - YEAR`), `matter_type` enum, `status` lifecycle (`intake` → `conflict_review` → `active` → `closed` → `archived`)
- `billing_review_staff_id`: single attorney responsible for bill approval
- `rate_card`: typed `RateCard` Pydantic model with `attorney` and `paralegal` optional hourly rates (stored as JSONB)
- `retainer_amount`, `refresh_trigger_pct` (default: 0.40, fraction 0–1)
- `is_pro_bono`: when True, all TIME entries are billed at $0.00
- Jurisdiction: `state` (default "Texas"), `county`, `court_name`, `matter_number`
- Dates: `fee_agreement_signed_date`, `opened_date`, `closed_date`
- `discovery_level`: Texas TRCP 190 discovery level (`level_1` | `level_2` | `level_3`)
- `notes`: internal notes, not visible to client

**Billing Splits**: `billing_splits` table holds `{matter_id, client_id, split_pct}` — must sum to 100%. DB trigger enforces this.

**Originating Attorneys**: `matter_staff` table with `split_pct` that must sum to 100% across originating records.

**Matter Rate Overrides**: `matter_rate_overrides` table holds per-staff hourly rates that override the matter's rate card for a specific timekeeper.

---

### 4.3 Staff Portal — Billing

**4.3.1 Billing Entry — Natural Language Interface**
- Free-text field, e.g.: `"bill .5 for reviewing discovery responses last Friday"`
- Input sent via `POST /api/v1/billing/parse` with the matter_id; backend:
  - Uses the **fast** LLM vendor to extract structured fields (hours, description, entry_type, billable, invoice_date)
  - Resolves the rate via `BillingService.resolve_rate()` and computes `amount = hours × rate`
  - Returns a flat response with `entry_type`, `description`, `hours`, `rate`, `amount`, `invoice_date`, `billable`, `confidence`
- LLM parses relative dates ("last Friday", "on April 3") into an ISO `invoice_date`
- Attorney reviews a preview card with an editable date picker, then **Commits** → writes to DB

**4.3.2 Billing Entry — Form Interface**
- Autocomplete: Matter → Timekeeper
- Entry types: `time`, `expense`, `flat_fee`
- `entry_date` is set server-side to today (when the entry was recorded)
- `invoice_date` is the date work was performed (defaults to today, editable)
- `staff_id` resolved from the JWT if not provided

**4.3.3 Rate Resolution**

Billing rate for a TIME entry is resolved in this order (in `BillingService.resolve_rate()`):

1. **Pro bono short-circuit:** if `matter.is_pro_bono` is True → rate=0, amount=0 immediately
2. `matter_rate_overrides` — per-staff, per-matter row
3. `matter.rate_card` (typed `RateCard` model with `attorney` / `paralegal` fields)
4. `staff.default_billing_rate`

Enforced in three places:

- Python: `BillingService.resolve_rate()` (primary)
- SQL function: `resolve_billing_rate()` (for reporting queries)
- DB trigger: `enforce_pro_bono_zero_rate` (backstop)

**4.3.4 Billing Review & Edit**
- Per-matter view of unbilled entries, sorted by `invoice_date`
- Billed entries cannot be edited — `prevent_billed_entry_edit` DB trigger as backstop

**4.3.5 Bill Production** *(Not yet built)*
- Attorney triggers bill generation; server compiles unbilled entries, applies billing splits, renders PDF (WeasyPrint planned), stores in Supabase Storage

---

### 4.4 Discovery Request Module (Staff)

The discovery module is structured in **two levels**: a parent document (the served set of requests) and individual numbered items within it.

**4.4.1 Database Shape**

- `discovery_requests` — parent document: `matter_id`, `ingested_by_staff_id`, `propounded_date`, `due_date`, `request_type` (`interrogatories` | `production` | `disclosures` | `admissions`), `look_back_date`, `response_served_date`, `storage_path`
- `discovery_request_items` — individual numbered request: `discovery_request_id` (FK to parent), `matter_id` (denormalized), `request_number`, `source_text` (verbatim markdown), `status`, `interpretations` (jsonb list), `privileges` (jsonb list of `{privilege_name, text}`), `objections` (jsonb list of `{objection_name, text}`), `client_response_needed` bool, `response` (nullable markdown)
- `standard_privileges`, `standard_objections` — seeded lookup tables. `standard_objections.applies_to` is a `TEXT[]` filter so only relevant objections appear for a given request type

**4.4.2 Ingestion Pipeline**

- `POST /api/v1/discovery/upload` accepts a multipart PDF upload
- `pdf_service.extract_text()` uses PyMuPDF for searchable pages; for image-only pages it renders to 300 DPI, enhances (grayscale, contrast 2.0, sharpness 1.5), and uses the LLM's vision capability for OCR. Tesseract is **not** used — LLM vision produces better results on legal documents
- `discovery_service.classify_document()` determines:
  - Type of request (interrogatories/production/disclosures/admissions)
  - Who propounded it — if our client, the upload is rejected
  - Service date from certificate of service
  - Number of days to respond (from the document itself; defaults to 30 if not specified)
  - Look-back date for responsive documents, if mentioned
- `discovery_service.extract_items()` extracts each numbered request verbatim as markdown
- Due date computed from service date + response days, rolled to Monday if it falls on a weekend
- Original PDF stored in Supabase Storage at `matters/{matter_id}/discovery/{document_id}.pdf`

**4.4.3 Attorney Review UI**

Each discovery request item can be expanded inline in the UI to edit:

1. `client_response_needed` toggle
2. Source text (for OCR cleanup)
3. **Privilege checkboxes** from `standard_privileges` — checking adds `{privilege_name: slug, text: template}`
4. **Objection checkboxes** from `standard_objections` filtered by request type — each selected objection exposes an editable textarea so the attorney can tailor the text
5. Interpretations list (add/edit/remove)
6. Attorney response (markdown textarea)

Status pill logic:

- Normally shows the item's DB status (`pending_client`, `pending_review`, `finalized`, `objected`)
- If `client_response_needed` is unchecked AND all response fields are empty, shows **"pending attorney"** (purple)
- If content exists (interpretations/privileges/objections/response), shows the underlying status

**4.4.4 Word Document Export**

`POST /api/v1/discovery/documents/{id}/download` returns a `.docx` with:

- Title and matter name
- One section per request item with: `Request #N: source_text`, Interpretations (numbered list), Privileges (name + text), Objections (name + text), Response
- Markdown in the response (`**bold**`, `*italic*`, numbered/bulleted lists) is parsed into native Word runs
- Hard line breaks in the response are preserved (each line = its own paragraph)

---

### 4.5 Pleading Ingestion Module (Staff)

The pleading module extracts case information, children, opposing counsel, and each party's claims and defenses from uploaded pleadings. These claims guide discovery drafting, objections, and witness examination.

**4.5.1 Database Shape**

- `matter_pleadings` — the parent document: `matter_id`, `opposing_party_id` (null means our client's pleading), `title`, `filed_date`, `served_date`, `amends_pleading_id` (self-ref for amendments), `is_supplement`, `storage_path`, `raw_text`, `ingested_by_staff_id`
- `matter_claims` — one row per extracted claim/defense/affirmative_defense/counterclaim: FK to the parent pleading, `matter_id` (denormalized), `opposing_party_id`, `kind`, `label`, `narrative`, `statute_rule_cited`
- `matter_children` — children of the marriage/relationship: `name` (FullName), `date_of_birth`, `sex`, `needs_support_after_majority` bool (for special-needs children unlikely to become self-supporting)
- `opposing_counsel` — **deduplicated by `UNIQUE(bar_state, bar_number)`** — full contact info (name, firm, full address, email, phone, fax, bar), `email_ccs` jsonb list for people to CC on correspondence
- `matter_opposing_counsel` — intersection table: `matter_id`, `opposing_counsel_id`, `opposing_party_id` (which OP they represent), `role`, `started_date`, `ended_date`

**Pleading lifecycle:** An "Original Petition for Divorce" is live until superseded by a "First Amended Petition for Divorce" (via `amends_pleading_id`). Supplements add to but do not supersede. A pleading is computed as "live" if no other pleading has `amends_pleading_id` pointing to it.

**OC dedup** is the key benefit: when an attorney moves firms, gets a new email, or changes their cell phone, the update propagates to all matters automatically because every matter references the same `opposing_counsel.id`.

**4.5.2 Stateless Preview/Commit Flow**

`POST /api/v1/pleadings/preview` (multipart PDF upload):

1. Extracts text via `pdf_service`
2. Runs **two** LLM calls:
   - `classify_and_extract` — extracts pleading metadata, case info, children, opposing counsel (with bar numbers)
   - `extract_claims` — extracts each distinct claim, defense, affirmative defense, counterclaim
3. Matches extracted OC against existing rows by `(bar_state, bar_number)` and computes field-level diffs for each match
4. Computes `matter_field_updates` — proposed changes to matter-level fields with `{current, proposed}` diffs
5. Returns a rich preview payload — **writes nothing to the database**

The attorney reviews the preview in the UI, edits anything wrong, adds claims the LLM may have missed, deselects matter field updates they don't want, and clicks Commit.

`POST /api/v1/pleadings/commit` writes:

- Matter field updates (only the accepted ones)
- New `matter_pleadings` row, PDF stored in `matters/{matter_id}/pleadings/{id}.pdf`
- New `matter_children` rows
- Opposing counsel rows (update existing or create new based on bar number) and `matter_opposing_counsel` links
- Extracted `matter_claims` rows

**4.5.3 Why extract our client's pleadings too?**

Our client's own pleadings populate claims that are later used as context when drafting discovery requests, objecting to opposing counsel's discovery, and building witness examination outlines. The `opposing_party_id` field on both `matter_pleadings` and `matter_claims` is nullable; null means "our client's."

---

### 4.6 Trust Ledger

- Append-only transaction log: deposits (positive), withdrawals (negative), refunds (negative)
- DB trigger `deny_trust_ledger_mutation` blocks UPDATE and DELETE — entries are immutable
- Running balance derived by summing all entries for a matter
- Balance endpoint: `GET /api/v1/billing/balance/{matter_id}` returns `trust_balance`, `unbilled_total`, net `balance`, and a `status` indicator (green/yellow/red)

### 4.7 Audit Log

- Append-only: DB trigger `deny_audit_log_mutation` blocks UPDATE and DELETE
- `AuditLogger` service inserts records — never re-raises on failure (prevents audit errors from crashing primary operations)
- Actions logged: billing entry CRUD, billing cycle closed, bill sent, fee agreement signed, trust ledger transaction, user role changed/correlated

---

## 5. UI Layout & Component Structure

### 5.1 Dual-Density Design

| Mode | Roles | Characteristics |
|------|-------|-----------------|
| **Relaxed** (Client Portal) | `client` | Generous padding, larger type, single-column on mobile, minimal chrome, guided step-by-step flows |
| **Compact** (Staff Portal) | `attorney`, `paralegal`, `admin` | Tighter grid, data-dense tables, sidebar nav, keyboard-friendly, more info per viewport |

Layout mode set by `AuthContext` via `document.body.dataset.density` based on authenticated role.

### 5.2 Implemented Component Hierarchy

```text
App
├── AuthProvider (Supabase session context, density management)
├── BrowserRouter
│   ├── Public routes
│   │   ├── / → LandingPage
│   │   ├── /login → LoginPage (Google OAuth)
│   │   ├── /auth/callback → AuthCallbackPage
│   │   ├── /onboarding → OnboardingPage (staff correlation)
│   │   ├── /access-denied → AccessDeniedPage
│   │   ├── /privacy → PrivacyPolicyPage   (for Google OAuth verification)
│   │   └── /terms → TermsOfUsePage        (for Google OAuth verification)
│   │
│   └── /app/* → ProtectedRoute
│       └── AppShell (sidebar nav, mobile hamburger, role-gated items)
│           ├── /app/dashboard → DashboardPage
│           ├── /app/billing → BillingPage     (NL parse + form entry)
│           ├── /app/matters → MattersPage     (CRUD + rate overrides)
│           ├── /app/clients → ClientsPage     (CRUD + conflict check)
│           ├── /app/discovery → DiscoveryPage (upload, review, Word export)
│           ├── /app/pleadings → PleadingsPage (upload, preview/commit review)
│           └── /app/admin → AdminPage         (staff management, admin-only)
```

### 5.3 Shared Types Architecture

All TypeScript types mirror backend Pydantic schemas and live in `frontend/src/types/`. One file per domain:

- `common.ts` — `FullName`
- `auth.ts` — `UserProfile`
- `client.ts` — `Client`, `ClientStatus`, `ClientCreatePayload`, `ConflictHit`
- `matter.ts` — `Matter`, `MatterStatus`, `MatterType`, `RateCard`, `MatterCreatePayload`, `RateOverride`
- `staff.ts` — `Staff`, `StaffRole`, `BarAdmission`
- `billing.ts` — `BillingEntry`, `EntryType`, `ParsedBillingPreview`
- `discovery.ts` — `DiscoveryDocument`, `DiscoveryRequestItem`, `StandardPrivilege`, `StandardObjection`, update payloads
- `pleading.ts` — `MatterPleading`, `MatterClaim`, `MatterChild`, `OpposingCounsel`, `PleadingIngestPreview`, commit entries

API functions in `api.ts` return typed promises (`Promise<Matter[]>` rather than `Promise<unknown[]>`). No `as Type[]` casts at call sites. Pages never redefine domain types locally.

### 5.4 Key Shared Components (To Build)

| Component | Purpose |
|-----------|---------|
| `<DataTable>` | Sortable, filterable, paginated; compact density in staff portal |
| `<StatusBadge>` | Color-coded pill for matter status, balance status, discovery item status |
| `<ClientBalanceWidget>` | Trust balance formula with red/yellow/green indicator |
| `<PDFDownloadButton>` | Fetches signed Supabase Storage URL, opens in new tab |
| `<StepWizard>` | Multi-step form shell with progress indicator (intake) |
| `<ConfirmDialog>` | Modal for destructive actions |

Currently every page implements its own inline tables and dialogs — these shared components are a future refactor.

---

## 6. Color Palette & Design Tokens

### 6.1 Brand Colors

| Token | Hex | Usage |
|-------|-----|-------|
| `navy` | `#003057` | Headers, sidebar background, primary buttons |
| `navy-light` | `#004a8f` | Hover on navy elements |
| `gold` | `#C9A84C` | Active nav, highlights, CTA borders |
| `gold-light` | `#e8c97a` | Hover on gold elements |
| `white` | `#FFFFFF` | Card backgrounds |
| `off-white` | `#F5F5F0` | Staff portal page background |

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

Google Fonts loaded in `frontend/index.html`. Font families mapped in `tailwind.config.js` as `font-display`, `font-sans`, `font-mono`.

---

## 7. Database Schema

### 7.1 Migration Files

All DDL lives in `db/migrations/` — run in numeric order:

| File | Purpose |
|------|---------|
| `001_extensions.sql` | `pg_trgm`, `pgcrypto` |
| `002_tables.sql` | Core tables |
| `003_indexes_triggers.sql` | Indexes; triggers for `updated_at`, immutability, pro bono, billing split validation |
| `004_functions.sql` | `search_conflicts()`, `resolve_billing_rate()`, `get_matter_balance()`; reporting views |
| `005_rls.sql` | Row-Level Security policies |
| `006_staff_auth_fields.sql` | `supabase_uid` nullable, `auth_email` UNIQUE on `staff` |
| `007_discovery_redesign.sql` | Renames `discovery_requests` → `discovery_request_items`; creates new parent `discovery_requests` table |
| `008_discovery_item_editing.sql` | Adds `response` column, `standard_privileges`, `standard_objections` (seeded) |
| `009_pleadings_and_oc.sql` | `matters.discovery_level`; `matter_children`, `opposing_counsel` (unique by bar number), `matter_opposing_counsel`, `matter_pleadings`, `matter_claims`; adds `storage_path` to `discovery_requests` |
| `run_all.sql` | Master script that runs all migrations in order |

### 7.2 Key Tables

| Table | Purpose | Immutable? |
|-------|---------|------------|
| `offices` | Firm office locations | No |
| `staff` | Attorneys, paralegals, admins | No |
| `clients` | Client records with intake details | No |
| `matters` | Legal matters | No |
| `matter_staff` | Originators + billing reviewer | No |
| `matter_rate_overrides` | Per-staff, per-matter hourly rate overrides | No |
| `matter_children` | Children of the marriage/relationship | No |
| `matter_pleadings` | Pleading documents (parent) | No |
| `matter_claims` | Claims/defenses extracted from pleadings | No |
| `opposing_counsel` | Opposing attorneys — dedup by `(bar_state, bar_number)` | No |
| `matter_opposing_counsel` | Matter ↔ OC intersection | No |
| `billing_splits` | Multi-client billing percentage assignments | No |
| `opposing_parties` | Named parties for conflict checking | No |
| `billing_entries` | Time, expense, flat-fee line items | Billed entries locked |
| `billing_cycles` | Billing periods with open/closed status | Closed cycles locked |
| `trust_ledger` | Append-only trust transactions | Yes |
| `fee_agreements` | Templates and executed agreements | No |
| `matter_events` | Deadlines, hearings, appointments | No |
| `discovery_requests` | Discovery document (parent) | No |
| `discovery_request_items` | Individual numbered requests | No |
| `discovery_responses` | Per-item responses (legacy — most editing is now inline on items) | No |
| `standard_privileges` | Seeded lookup for privilege assertions | No |
| `standard_objections` | Seeded lookup for objections (filterable by request type) | No |
| `user_roles` | Auth entry point | No |
| `audit_log` | Immutable log of sensitive actions | Yes |

### 7.3 Key Triggers

| Trigger | Table | Purpose |
|---------|-------|---------|
| `set_updated_at` | Many tables | Auto-set `updated_at` on UPDATE |
| `deny_trust_ledger_mutation` | `trust_ledger` | Block UPDATE/DELETE |
| `deny_audit_log_mutation` | `audit_log` | Block UPDATE/DELETE |
| `enforce_pro_bono_zero_rate` | `billing_entries` | Force rate=0 for TIME entries on pro bono matters |
| `prevent_billed_entry_edit` | `billing_entries` | Block changes to billed entries |
| `validate_billing_splits_sum` | `billing_splits` | Ensure splits sum to 100% per matter |
| `validate_originating_split_sum` | `matter_staff` | Ensure origination splits sum to 100% per matter |

### 7.4 Key Functions & Views

| Function | Purpose |
|----------|---------|
| `search_conflicts(query, threshold)` | Trigram similarity search across clients and opposing parties |
| `resolve_billing_rate(p_matter_id, p_staff_id)` | SQL rate resolution |
| `get_matter_balance(p_matter_id)` | Trust balance minus unbilled entries |

| View | Purpose |
|------|---------|
| `v_matter_summary` | Matter with client name, staff count, entry count |
| `v_unbilled_entries` | All unbilled entries with staff and matter names |
| `v_discovery_progress` | Per-matter discovery response completion stats |

### 7.5 Supabase Storage

- Single bucket: `matter-documents` (private, signed URLs only)
- Path convention:
  - `matters/{matter_id}/pleadings/{pleading_id}.pdf`
  - `matters/{matter_id}/discovery/{document_id}.pdf`
- Created manually in the Supabase dashboard; not part of the migration DDL
- All access goes through `services/storage_service.py` — never touch `supabase.storage` directly elsewhere

---

## 8. Runtime Parameters & Environment Configuration

### 8.1 Settings Class (`app/util/settings.py`)

All runtime parameters managed through `Settings(BaseSettings)`. Module-level singleton: `settings = Settings()`.

See [CLAUDE.md §5](./CLAUDE.md#5-settings-apputilsettingspy) for the authoritative field list — the table drifts easily so that's the single source of truth.

### 8.2 Environment Files

| File | Purpose | Committed? |
|------|---------|------------|
| `.env` | Local development secrets | Never |
| `.env.example` | Template with all keys, no values | Yes |
| `.env.frontend.example` | Frontend-only env var template | Yes |
| `docker-compose.override.yml` | Dev overrides | Yes (no secrets) |

### 8.3 Frontend Environment Variables

React env vars prefixed `VITE_` and baked in at build time via Docker build args:

| Variable | Purpose |
|----------|---------|
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Safe public key |
| `VITE_API_BASE_URL` | FastAPI backend URL |

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
| `WARNING` | LLM parse failure, Stripe webhook mismatch, conflict check error |
| `ERROR` | Database operation failed, unhandled exception, LLM API call failure |
| `CRITICAL` | System cannot start, missing required config |

### 9.3 Rules

- Use `%s` format args in all log calls — never f-strings
- **No PII in log messages.** Reference records by database ID only
- `supabasemanager.py` pins `httpx` and `postgrest` to `DEBUG` at module load

---

## 10. API Integration — LLM, PDF, Storage, FastAPI

### 10.1 FastAPI Route Map

**Public (no auth):**

| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/config` | Public config (Stripe key, firm name, version) |

**Authenticated (no role required):**

| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/auth/me` | Current user's role profile (404 if none) |
| `POST` | `/api/v1/auth/correlate-staff` | First-login correlation |

**Staff routes** (`attorney` | `paralegal` | `admin` unless noted):

| Method | Path | Purpose |
|--------|------|---------|
| `GET/POST/PATCH/DELETE` | `/api/v1/staff[/id]` | Staff CRUD |
| `GET/POST/PATCH` | `/api/v1/clients[/id]` | Client CRUD |
| `POST` | `/api/v1/clients/conflict-check` | Conflict of interest search |
| `GET/POST/PATCH/DELETE` | `/api/v1/matters[/id]` | Matter CRUD (create/delete = attorney/admin only) |
| `GET/POST/DELETE` | `/api/v1/matters/{id}/rate-overrides[/oid]` | Rate override management |
| `GET/POST` | `/api/v1/billing/entries` | List / create billing entries |
| `PATCH/DELETE` | `/api/v1/billing/entries/{id}` | Update / delete entry |
| `GET` | `/api/v1/billing/balance/{matter_id}` | Client financial balance |
| `POST` | `/api/v1/billing/parse` | NL billing parse with rate resolution |
| `GET/POST` | `/api/v1/billing/cycles` | List / create billing cycles |
| `POST` | `/api/v1/billing/cycles/{id}/close` | Close billing cycle |
| `GET` | `/api/v1/discovery/{matter_id}/documents` | List discovery documents for a matter |
| `PATCH` | `/api/v1/discovery/documents/{id}` | Update document metadata |
| `GET` | `/api/v1/discovery/documents/{id}/items` | List items for a document |
| `GET` | `/api/v1/discovery/documents/{id}/download` | Download Word export |
| `POST` | `/api/v1/discovery/upload` | Upload discovery PDF (multipart) |
| `PATCH` | `/api/v1/discovery/items/{id}` | Edit an item (privileges, objections, response, etc.) |
| `GET` | `/api/v1/discovery/standard-privileges` | Seeded privilege list |
| `GET` | `/api/v1/discovery/standard-objections?request_type=X` | Seeded objections filtered by type |
| `GET/PATCH` | `/api/v1/discovery/responses/{id}` | Legacy response editing |
| `POST` | `/api/v1/pleadings/preview` | Upload PDF, return LLM extraction preview |
| `POST` | `/api/v1/pleadings/commit` | Commit reviewed preview |
| `GET` | `/api/v1/matters/{id}/pleadings` | List pleadings |
| `GET/PATCH` | `/api/v1/pleadings/{id}` | Pleading metadata |
| `GET` | `/api/v1/pleadings/{id}/download` | Signed URL for the original PDF |
| `GET` | `/api/v1/matters/{id}/claims` | List claims for a matter |
| `PATCH/DELETE` | `/api/v1/claims/{id}` | Edit/delete a claim |
| `GET` | `/api/v1/matters/{id}/children` | List children |
| `GET` | `/api/v1/matters/{id}/opposing-counsel` | List OC linked to a matter |
| `PATCH` | `/api/v1/opposing-counsel/{id}` | Update OC record (propagates to all matters via FK) |

**Admin-only routes:**

| Method | Path | Purpose |
|--------|------|---------|
| `GET/POST/PATCH` | `/api/v1/admin/user-roles[/id]` | List / assign / update roles |
| `GET` | `/api/v1/admin/audit-log` | Query audit log |

### 10.2 Services Layer

All business logic lives in `app/services/`. Current services:

| Service | Purpose |
|---------|---------|
| `llm_service.py` | Multi-vendor LLM dispatch. `complete`, `complete_fast`, `complete_with_image` (vision). Vendors: anthropic, gemini, openai, groq, deepseek. Vision support: anthropic, gemini, openai. Lazy imports per vendor. |
| `pdf_service.py` | PyMuPDF text extraction + LLM vision fallback for scanned pages. Image enhancement (grayscale, contrast 2.0, sharpness 1.5) before vision call. |
| `storage_service.py` | Supabase Storage wrapper. Matter-scoped paths. Signed URL generation. |
| `docx_service.py` | Word document generation for discovery response export. Parses markdown into native Word runs. Preserves hard line breaks. |
| `billing_service.py` | Rate resolution, pro bono enforcement, natural-language parse, cycle closure, balance calculation. |
| `discovery_service.py` | Two-step LLM pipeline: `classify_document` + `extract_items`. Strips markdown code fences from LLM JSON responses. |
| `pleading_service.py` | Stateless preview/commit pipeline. OC dedup by bar number with field-level diffs. |
| `conflict_service.py` | Phase 1 substring match; Phase 2 pg_trgm architecture ready. |
| `audit_logger.py` | Fail-safe audit logging — never re-raises. |

### 10.3 Middleware Stack (in order)

1. `CORSMiddleware` — configured per environment
2. `AuthMiddleware` — validates Supabase JWT via JWKS/ES256; injects `supabase_uid`, `role`, `email` into `request.state`
3. Route-level `Depends(require_role([...]))` — authoritative role check against `user_roles` table

---

## 11. Docker & Deployment

### 11.1 Services

```yaml
# docker-compose.yml (production)
services:
  api:
    image: ghcr.io/tjdaley/jdbot-cyclone-api:X.Y.Z
    build: ./app
    expose: ["8000"]          # internal-only; nginx proxies from frontend
    env_file: .env
    volumes: ["./.env:/app/.env:ro"]
    healthcheck:              # python urllib against /api/health
      ...

  frontend:
    image: ghcr.io/tjdaley/jdbot-cyclone-frontend:X.Y.Z
    build:
      context: ./frontend
      args:
        VITE_SUPABASE_URL: ${SUPABASE_URL}
        VITE_SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
        VITE_API_BASE_URL: ${API_BASE_URL:-}
    ports: ["8094:80"]        # haproxy-fronted on host port 8094
    healthcheck:              # wget --spider http://127.0.0.1:80/
      ...
    depends_on: [api]
```

```yaml
# docker-compose.override.yml (dev — auto-applied)
services:
  api:
    ports: ["8000:8000"]
    volumes: ["./app:/app"]
    environment: [IS_DEVELOPMENT=true, LOG_LEVEL=DEBUG]
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    ports: ["3000:80"]
    volumes: ["./frontend:/app", "/app/node_modules"]
    environment: [VITE_API_BASE_URL=http://localhost:8000]
```

### 11.2 nginx Config

The frontend's `nginx.conf` (inside the container) handles:

- SPA fallback: `try_files $uri $uri/ /index.html`
- `/api/*` proxy to `http://api:8000`
- `proxy_read_timeout 300s` for LLM-powered endpoints (pleading preview, discovery upload can take 60+ seconds)
- `client_max_body_size 50m` for large PDF uploads

### 11.3 Deployment Notes

- Tagged images published to `ghcr.io/tjdaley/jdbot-cyclone-*`
- Production runs behind haproxy on the host at port 8094 — the API container has `expose: "8000"` (not `ports`) and is only reachable via the Docker network
- The `.env` file is both `env_file`-injected AND bind-mounted at `/app/.env:ro` so Pydantic's `env_file = ".env"` can also read it
- Healthchecks use `127.0.0.1` literal (not `localhost`) because Alpine's IPv6-first `localhost` resolution breaks when nginx listens only on IPv4

### 11.4 Running Locally Without Docker

```bash
# Terminal 1 — backend
uvicorn main:app --app-dir app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

---

## 12. Implementation Status

### Fully Implemented

- **Models + repositories + schemas** for all current domains: staff, client, matter, billing, trust ledger, fee agreement, matter event, discovery (documents + items), pleading (pleadings, claims, children, opposing counsel), user role, audit log
- **`SupabaseManager`** with retry logic and automatic `datetime`/`Enum` JSON normalization in `insert`/`update`
- **`AuthMiddleware`** with JWT validation via JWKS/ES256
- **`require_role()`** with authoritative DB-based role check
- **`LLMService`** with 5-vendor dispatch, `complete` / `complete_fast` / `complete_with_image` (vision)
- **`PDFService`** with PyMuPDF + LLM vision fallback
- **`StorageService`** wrapping Supabase Storage
- **`DocxService`** for discovery response Word export with markdown parsing
- **`BillingService`** with rate resolution, pro bono, NL parsing (including LLM-parsed `invoice_date`)
- **`DiscoveryService`** — two-step LLM pipeline for discovery ingestion
- **`PleadingService`** — stateless preview/commit with OC dedup by bar number
- **`ConflictService`** (Phase 1 substring)
- **`AuditLogger`** with fail-safe logging
- **Migrations 001–009** covering all current tables, triggers, functions, RLS, and lookup seeds
- **Frontend shared types** for all domains — no per-page type definitions; api.ts returns typed promises
- **Staff portal pages**:
  - Dashboard with stats
  - Clients (CRUD, conflict check with typed referral metadata)
  - Matters (CRUD with rate overrides, short_name auto-generation)
  - Billing (NL parse → preview with rate/amount/invoice_date → commit; entries table)
  - Discovery (drag-drop PDF upload → ingest → inline editing with privilege/objection checkboxes → Word export)
  - Pleadings (drag-drop PDF upload → preview → review/edit → commit; claims summary)
  - Admin (staff management)
- **Privacy policy and Terms of Use pages** for Google OAuth verification
- **Docker Compose** with production tagged images, dev override, healthchecks, and haproxy-compatible port mapping

### Not Yet Implemented

- **Client Portal** — `client` role exists but currently redirects to `/access-denied`
- **Fee agreement templates and electronic signature** — model exists, no UI
- **PDF bill generation** (WeasyPrint)
- **Stripe checkout and webhooks** — keys configured, no handlers
- **Email notifications**
- **Client intake form / StepWizard**
- **File upload for receipts** on expense billing entries
- **Reusable shared components** (`DataTable`, `StatusBadge`, `ConfirmDialog`, `ClientBalanceWidget`)
- **Test suite** (unit and integration tests)
- **Phase 2 conflict checking** (pg_trgm RPC wiring)
- **Auto-generation of objections and privileges** from claims (the tables and fields exist; the generation logic is the "after that" in the discovery/pleading roadmap)
- **Witness examination outlines** and **discovery drafting** powered by matter claims (the data collection is built; the consumers are not)

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

### 13.3 Claims-Powered Discovery Drafting

- Once enough matter claims are collected across pleadings, the system will use them as context for:
  - Drafting discovery requests targeted at the other side's stated claims
  - Auto-suggesting objections when responding to opposing counsel's discovery (matching their requests against the scope defined by our claims)
  - Generating witness examination outlines keyed to specific claims and the witnesses best positioned to support/rebut them

### 13.4 Reporting / Analytics

- Expose PostgreSQL to **Metabase** rather than building custom reports
- Reserve a read-only `reporting` Postgres role for Metabase access

### 13.5 Electronic Signature Integration

- Replace checkbox acknowledgment with DocuSign or HelloSign for fee agreements

### 13.6 Calendar Integration

- Sync `matter_events` with attorney calendars (Google Calendar, Outlook) via OAuth

---

*End of Cyclone PRD v3.0*

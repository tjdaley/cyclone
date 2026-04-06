-- =============================================================================
-- 002_tables.sql
-- All Cyclone domain tables in dependency order (referenced tables first).
-- Run after 001_extensions.sql.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- OFFICES
-- Referenced by staff.office_id. A law firm may have multiple offices.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS offices (
    id          SERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    address     TEXT,
    telephone   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- STAFF
-- Attorneys, paralegals, and admins. Links to Supabase auth.users via
-- supabase_uid. name and bar_admissions are stored as JSONB because they are
-- structured objects (FullName / BarAdmission[]) serialised by Pydantic.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staff (
    id                   SERIAL PRIMARY KEY,
    supabase_uid         TEXT        NOT NULL UNIQUE,
    role                 TEXT        NOT NULL
                             CHECK (role IN ('attorney', 'paralegal', 'admin')),
    name                 JSONB       NOT NULL,       -- FullName object
    office_id            INTEGER     NOT NULL REFERENCES offices (id),
    email                TEXT        NOT NULL UNIQUE,
    telephone            TEXT        NOT NULL,
    slug                 TEXT        NOT NULL UNIQUE,
    bar_admissions       JSONB       NOT NULL DEFAULT '[]'::jsonb,  -- BarAdmission[]
    default_billing_rate NUMERIC(10, 2),             -- NULL for admin roles
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- CLIENTS
-- name stored as JSONB (FullName). Conflict checks use pg_trgm via the
-- name_to_text() function defined in 004_functions.sql.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    id              SERIAL PRIMARY KEY,
    name            JSONB       NOT NULL,            -- FullName object
    email           TEXT        NOT NULL UNIQUE,
    telephone       TEXT        NOT NULL,
    referral_source TEXT,
    status          TEXT        NOT NULL DEFAULT 'prospect'
                        CHECK (status IN (
                            'prospect', 'pending_conflict_check',
                            'conflict_flagged', 'active', 'inactive'
                        )),
    prior_counsel   TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- MATTERS
-- One primary client per matter. Multi-client billing is handled via
-- billing_splits. rate_card is JSONB keyed by role name or staff_id string.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matters (
    id                      SERIAL PRIMARY KEY,
    client_id               INTEGER     NOT NULL REFERENCES clients (id),
    matter_name             TEXT        NOT NULL,
    matter_type             TEXT        NOT NULL
                                CHECK (matter_type IN (
                                    'divorce', 'child_custody', 'modification',
                                    'enforcement', 'cps', 'probate',
                                    'estate_planning', 'civil', 'other'
                                )),
    status                  TEXT        NOT NULL DEFAULT 'intake'
                                CHECK (status IN (
                                    'intake', 'conflict_review', 'active',
                                    'closed', 'archived'
                                )),
    billing_review_staff_id INTEGER     REFERENCES staff (id),
    rate_card               JSONB       NOT NULL DEFAULT '{}'::jsonb,
    retainer_amount         NUMERIC(12, 2) NOT NULL DEFAULT 0.00
                                CHECK (retainer_amount >= 0),
    refresh_trigger_pct     NUMERIC(5, 4) NOT NULL DEFAULT 0.40
                                CHECK (refresh_trigger_pct BETWEEN 0 AND 1),
    is_pro_bono             BOOLEAN     NOT NULL DEFAULT FALSE,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- MATTER_STAFF
-- Many-to-many: staff members assigned to a matter with a role.
-- Originators carry split_pct that should sum to 100 across the matter
-- (enforced by the check_originating_split_pct trigger in 003).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matter_staff (
    id         SERIAL PRIMARY KEY,
    matter_id  INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    staff_id   INTEGER     NOT NULL REFERENCES staff (id),
    role       TEXT        NOT NULL
                   CHECK (role IN ('originating', 'billing_reviewer', 'assigned')),
    split_pct  NUMERIC(5, 2)
                   CHECK (split_pct IS NULL OR (split_pct >= 0 AND split_pct <= 100)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (matter_id, staff_id, role)
);

-- ---------------------------------------------------------------------------
-- BILLING_SPLITS
-- Per-matter percentage allocation across multiple clients.
-- Splits must sum to 100 for a given matter (enforced by trigger in 003).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_splits (
    id         SERIAL PRIMARY KEY,
    matter_id  INTEGER       NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    client_id  INTEGER       NOT NULL REFERENCES clients (id),
    split_pct  NUMERIC(5, 2) NOT NULL
                   CHECK (split_pct > 0 AND split_pct <= 100),
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (matter_id, client_id)
);

-- ---------------------------------------------------------------------------
-- OPPOSING_PARTIES
-- Parties adverse to a client on a matter. Used by the conflict-check
-- service (pg_trgm search on full_name).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS opposing_parties (
    id           SERIAL PRIMARY KEY,
    matter_id    INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    full_name    TEXT        NOT NULL,
    relationship TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- MATTER_RATE_OVERRIDES
-- Per-matter hourly rate override for an individual staff member.
-- Resolution order in BillingService: override → rate_card → staff default.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matter_rate_overrides (
    id         SERIAL PRIMARY KEY,
    matter_id  INTEGER       NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    staff_id   INTEGER       NOT NULL REFERENCES staff (id),
    rate       NUMERIC(10, 2) NOT NULL CHECK (rate >= 0),
    created_at TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ,
    UNIQUE (matter_id, staff_id)
);

-- ---------------------------------------------------------------------------
-- BILLING_CYCLES
-- Groups billing entries for a matter into a period. Closing a cycle
-- generates the PDF bill and locks entries (billed = TRUE).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_cycles (
    id                  SERIAL PRIMARY KEY,
    matter_id           INTEGER     NOT NULL REFERENCES matters (id),
    period_start        DATE        NOT NULL,
    period_end          DATE        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'closed')),
    closed_by_staff_id  INTEGER     REFERENCES staff (id),
    bill_storage_path   TEXT,
    stripe_payment_link TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ,
    CONSTRAINT period_order CHECK (period_end >= period_start)
);

-- ---------------------------------------------------------------------------
-- BILLING_ENTRIES
-- Time, expense, and flat-fee entries. Linked to a cycle when billed.
-- For TIME entries on pro-bono matters, amount = 0 (enforced in app layer
-- and by the pro_bono_zero_rate trigger in 003_triggers.sql).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_entries (
    id                   SERIAL PRIMARY KEY,
    matter_id            INTEGER       NOT NULL REFERENCES matters (id),
    staff_id             INTEGER       NOT NULL REFERENCES staff (id),
    billing_cycle_id     INTEGER       REFERENCES billing_cycles (id),
    entry_type           TEXT          NOT NULL
                             CHECK (entry_type IN ('time', 'expense', 'flat_fee')),
    entry_date           DATE          NOT NULL,
    hours                NUMERIC(6, 2) CHECK (hours IS NULL OR hours >= 0),
    rate                 NUMERIC(10, 2) CHECK (rate IS NULL OR rate >= 0),
    amount               NUMERIC(12, 2) CHECK (amount IS NULL OR amount >= 0),
    description          TEXT          NOT NULL,
    billable             BOOLEAN       NOT NULL DEFAULT TRUE,
    billed               BOOLEAN       NOT NULL DEFAULT FALSE,
    receipt_storage_path TEXT,
    created_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ,
    -- Entry-type field constraints (mirrors BillingEntry model validators)
    CONSTRAINT time_requires_hours    CHECK (entry_type != 'time'    OR hours IS NOT NULL),
    CONSTRAINT expense_requires_amount CHECK (entry_type != 'expense'  OR amount IS NOT NULL),
    CONSTRAINT flat_fee_requires_amount CHECK (entry_type != 'flat_fee' OR amount IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- TRUST_LEDGER
-- Append-only trust account transaction log. Balance is derived at query
-- time by summing all entries for a matter (deposits positive, others
-- negative). No updates or deletes should ever be performed on this table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trust_ledger (
    id                 SERIAL PRIMARY KEY,
    matter_id          INTEGER       NOT NULL REFERENCES matters (id),
    transaction_type   TEXT          NOT NULL
                           CHECK (transaction_type IN ('deposit', 'withdrawal', 'refund')),
    amount             NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    transaction_date   DATE          NOT NULL,
    description        TEXT          NOT NULL,
    posted_by_staff_id INTEGER       NOT NULL REFERENCES staff (id),
    reference_number   TEXT,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT now()
    -- No updated_at: trust ledger entries are immutable
);

-- ---------------------------------------------------------------------------
-- FEE_AGREEMENTS
-- Phase 1: checkbox acknowledgment. Phase 2: external e-signature.
-- template_id references a future fee_agreement_templates table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fee_agreements (
    id                     SERIAL PRIMARY KEY,
    matter_id              INTEGER       NOT NULL REFERENCES matters (id),
    template_id            INTEGER,      -- future FK to fee_agreement_templates(id)
    status                 TEXT          NOT NULL DEFAULT 'draft'
                               CHECK (status IN (
                                   'draft', 'sent_to_client', 'executed', 'voided'
                               )),
    retainer_amount        NUMERIC(12, 2) NOT NULL CHECK (retainer_amount >= 0),
    refresh_trigger_pct    NUMERIC(5, 4)  NOT NULL DEFAULT 0.40
                               CHECK (refresh_trigger_pct BETWEEN 0 AND 1),
    signed_at              TIMESTAMPTZ,
    signed_by_supabase_uid TEXT,
    storage_path           TEXT,
    external_signature_id  TEXT,
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- MATTER_EVENTS
-- Hearings, deadlines, appointments. Read-only from the client portal.
-- Future: sync with Google Calendar / Outlook (PRD §11.5).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matter_events (
    id                  SERIAL PRIMARY KEY,
    matter_id           INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    event_type          TEXT        NOT NULL
                            CHECK (event_type IN (
                                'hearing', 'deposition', 'deadline',
                                'mediation', 'appointment', 'other'
                            )),
    title               TEXT        NOT NULL,
    description         TEXT,
    event_date          DATE        NOT NULL,
    event_time          TIME,
    location            TEXT,
    created_by_staff_id INTEGER     NOT NULL REFERENCES staff (id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- DISCOVERY_REQUESTS
-- Individual discovery items ingested by the LLM parser. One row per
-- request number within a matter. Uniqueness enforced per (matter, type, number).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_requests (
    id                   SERIAL PRIMARY KEY,
    matter_id            INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    request_type         TEXT        NOT NULL
                             CHECK (request_type IN (
                                 'interrogatory', 'rfa', 'rfp', 'witness_list'
                             )),
    request_number       INTEGER     NOT NULL CHECK (request_number >= 1),
    source_text          TEXT        NOT NULL,
    status               TEXT        NOT NULL DEFAULT 'pending_client'
                             CHECK (status IN (
                                 'pending_client', 'pending_review', 'finalized', 'objected'
                             )),
    ingested_by_staff_id INTEGER     NOT NULL REFERENCES staff (id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ,
    UNIQUE (matter_id, request_type, request_number)
);

-- ---------------------------------------------------------------------------
-- DISCOVERY_RESPONSES
-- One response record per discovery_request. Combines client draft and
-- attorney edits, objections, and finalization in a single row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_responses (
    id                       SERIAL PRIMARY KEY,
    discovery_request_id     INTEGER     NOT NULL UNIQUE
                                 REFERENCES discovery_requests (id) ON DELETE CASCADE,
    client_response_text     TEXT,
    rfa_selection            TEXT
                                 CHECK (rfa_selection IS NULL OR rfa_selection IN (
                                     'admit', 'deny', 'insufficient_information'
                                 )),
    has_responsive_documents BOOLEAN,
    attorney_objection       TEXT,
    privilege_claimed        BOOLEAN     NOT NULL DEFAULT FALSE,
    attorney_note            TEXT,
    final_response_text      TEXT,
    is_final                 BOOLEAN     NOT NULL DEFAULT FALSE,
    last_updated_by_uid      TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- USER_ROLES
-- Maps Supabase Auth UIDs to application roles. FastAPI require_role()
-- queries this table as the authoritative source; JWT claims are a hint only.
-- Constraint: staff roles link to staff; client role links to clients.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_roles (
    id           SERIAL PRIMARY KEY,
    supabase_uid TEXT        NOT NULL UNIQUE,
    role         TEXT        NOT NULL
                     CHECK (role IN ('client', 'attorney', 'paralegal', 'admin')),
    staff_id     INTEGER     REFERENCES staff (id),
    client_id    INTEGER     REFERENCES clients (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ,
    CONSTRAINT role_link_check CHECK (
        (role IN ('attorney', 'paralegal', 'admin')
            AND staff_id IS NOT NULL
            AND client_id IS NULL)
        OR
        (role = 'client'
            AND client_id IS NOT NULL
            AND staff_id IS NULL)
    )
);

-- ---------------------------------------------------------------------------
-- AUDIT_LOG
-- Immutable, append-only compliance log. UUID primary key to prevent
-- sequential enumeration. No UPDATE or DELETE should ever touch this table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    supabase_uid TEXT,
    action       TEXT        NOT NULL,   -- e.g. 'billing_entry.created'
    entity_type  TEXT        NOT NULL,   -- e.g. 'billing_entry'
    entity_id    TEXT,                   -- string PK of the affected record
    before_json  JSONB,
    after_json   JSONB
);

-- =============================================================================
-- 003_indexes_triggers.sql
-- Indexes for query performance and triggers for data integrity.
-- Run after 002_tables.sql.
-- =============================================================================


-- =============================================================================
-- SECTION 1: AUTO-UPDATE updated_at TRIGGER
-- Applied to every table that has an updated_at column.
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Apply to all tables with updated_at
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'offices', 'staff', 'clients', 'matters',
        'matter_rate_overrides', 'billing_cycles', 'billing_entries',
        'fee_agreements', 'matter_events', 'discovery_requests',
        'discovery_responses', 'user_roles'
    ] LOOP
        EXECUTE format(
            'CREATE OR REPLACE TRIGGER trg_%I_updated_at
             BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION set_updated_at()',
            t, t
        );
    END LOOP;
END;
$$;


-- =============================================================================
-- SECTION 2: TRUST LEDGER IMMUTABILITY GUARD
-- Prevents UPDATE and DELETE on trust_ledger rows.
-- =============================================================================

CREATE OR REPLACE FUNCTION deny_trust_ledger_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'trust_ledger rows are immutable. '
        'Post a correcting entry instead of modifying id=%', OLD.id;
END;
$$;

CREATE TRIGGER trg_trust_ledger_no_update
    BEFORE UPDATE ON trust_ledger
    FOR EACH ROW EXECUTE FUNCTION deny_trust_ledger_mutation();

CREATE TRIGGER trg_trust_ledger_no_delete
    BEFORE DELETE ON trust_ledger
    FOR EACH ROW EXECUTE FUNCTION deny_trust_ledger_mutation();


-- =============================================================================
-- SECTION 3: AUDIT LOG IMMUTABILITY GUARD
-- Prevents UPDATE and DELETE on audit_log rows.
-- =============================================================================

CREATE OR REPLACE FUNCTION deny_audit_log_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log rows are immutable. id=%', OLD.id;
END;
$$;

CREATE TRIGGER trg_audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION deny_audit_log_mutation();

CREATE TRIGGER trg_audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION deny_audit_log_mutation();


-- =============================================================================
-- SECTION 4: PRO-BONO BILLING ENTRY TRIGGER
-- Enforces $0.00 rate and amount on TIME entries for pro-bono matters.
-- The app layer does the same check; this is the database backstop.
-- =============================================================================

CREATE OR REPLACE FUNCTION enforce_pro_bono_zero_rate()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_is_pro_bono BOOLEAN;
BEGIN
    IF NEW.entry_type = 'time' THEN
        SELECT is_pro_bono INTO v_is_pro_bono
        FROM matters WHERE id = NEW.matter_id;

        IF v_is_pro_bono THEN
            NEW.rate   := 0.00;
            NEW.amount := 0.00;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_billing_entries_pro_bono
    BEFORE INSERT OR UPDATE ON billing_entries
    FOR EACH ROW EXECUTE FUNCTION enforce_pro_bono_zero_rate();


-- =============================================================================
-- SECTION 5: BILLING_SPLITS SUM VALIDATION
-- Ensures split percentages for a matter always sum to exactly 100.
-- Fires AFTER INSERT/UPDATE/DELETE so all sibling rows are visible.
-- =============================================================================

CREATE OR REPLACE FUNCTION validate_billing_splits_sum()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_matter_id INTEGER;
    v_total     NUMERIC;
BEGIN
    v_matter_id := COALESCE(NEW.matter_id, OLD.matter_id);
    SELECT COALESCE(SUM(split_pct), 0) INTO v_total
    FROM billing_splits WHERE matter_id = v_matter_id;

    -- Allow partial states while building up splits; only reject > 100
    IF v_total > 100 THEN
        RAISE EXCEPTION
            'billing_splits for matter_id=% sum to %, exceeding 100%%',
            v_matter_id, v_total;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_billing_splits_sum
    AFTER INSERT OR UPDATE OR DELETE ON billing_splits
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_billing_splits_sum();


-- =============================================================================
-- SECTION 6: ORIGINATING STAFF SPLIT VALIDATION
-- Ensures originating split_pct values for a matter sum to exactly 100
-- once all originating assignments are in place.
-- Deferrable so bulk inserts can complete before validation fires.
-- =============================================================================

CREATE OR REPLACE FUNCTION validate_originating_split_sum()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
DECLARE
    v_matter_id INTEGER;
    v_total     NUMERIC;
    v_count     INTEGER;
BEGIN
    v_matter_id := COALESCE(NEW.matter_id, OLD.matter_id);

    IF COALESCE(NEW.role, OLD.role) != 'originating' THEN
        RETURN NULL;
    END IF;

    SELECT COALESCE(SUM(split_pct), 0), COUNT(*)
    INTO v_total, v_count
    FROM matter_staff
    WHERE matter_id = v_matter_id AND role = 'originating';

    -- Only reject if any originating rows exist and they exceed 100
    IF v_count > 0 AND v_total > 100 THEN
        RAISE EXCEPTION
            'Originating split_pct for matter_id=% sums to %, exceeding 100%%',
            v_matter_id, v_total;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_matter_staff_originating_split
    AFTER INSERT OR UPDATE OR DELETE ON matter_staff
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validate_originating_split_sum();


-- =============================================================================
-- SECTION 7: PREVENT EDITING BILLED ENTRIES
-- Once billed = TRUE a billing entry must not be modified.
-- The app layer also blocks this; this is the database backstop.
-- =============================================================================

CREATE OR REPLACE FUNCTION prevent_billed_entry_edit()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.billed = TRUE AND NEW.billed = TRUE THEN
        -- Allow only billing_cycle_id and billed flag changes during cycle close
        IF OLD.description   != NEW.description   OR
           OLD.hours         IS DISTINCT FROM NEW.hours    OR
           OLD.rate          IS DISTINCT FROM NEW.rate     OR
           OLD.amount        IS DISTINCT FROM NEW.amount   OR
           OLD.entry_type    != NEW.entry_type    OR
           OLD.entry_date    != NEW.entry_date    OR
           OLD.billable      != NEW.billable
        THEN
            RAISE EXCEPTION
                'Cannot edit a billed billing_entry: id=%', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_billing_entries_no_edit_billed
    BEFORE UPDATE ON billing_entries
    FOR EACH ROW EXECUTE FUNCTION prevent_billed_entry_edit();


-- =============================================================================
-- SECTION 8: PERFORMANCE INDEXES
-- =============================================================================

-- ── staff ────────────────────────────────────────────────────────────────────
-- UNIQUE indexes on supabase_uid, email, slug already created by constraints.
CREATE INDEX IF NOT EXISTS idx_staff_office_id ON staff (office_id);
CREATE INDEX IF NOT EXISTS idx_staff_role      ON staff (role);

-- ── clients ──────────────────────────────────────────────────────────────────
-- UNIQUE index on email already created by constraint.
CREATE INDEX IF NOT EXISTS idx_clients_status     ON clients (status);
CREATE INDEX IF NOT EXISTS idx_clients_created_at ON clients (created_at DESC);

-- ── matters ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_matters_client_id  ON matters (client_id);
CREATE INDEX IF NOT EXISTS idx_matters_status     ON matters (status);
CREATE INDEX IF NOT EXISTS idx_matters_created_at ON matters (created_at DESC);

-- ── matter_staff ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_matter_staff_matter_id ON matter_staff (matter_id);
CREATE INDEX IF NOT EXISTS idx_matter_staff_staff_id  ON matter_staff (staff_id);

-- ── billing_splits ───────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_billing_splits_matter_id ON billing_splits (matter_id);

-- ── opposing_parties ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_opposing_parties_matter_id ON opposing_parties (matter_id);

-- ── matter_rate_overrides ────────────────────────────────────────────────────
-- UNIQUE (matter_id, staff_id) already covers the common query pattern.

-- ── billing_cycles ───────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_billing_cycles_matter_id ON billing_cycles (matter_id);
CREATE INDEX IF NOT EXISTS idx_billing_cycles_status    ON billing_cycles (matter_id, status);

-- ── billing_entries ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_billing_entries_matter_id       ON billing_entries (matter_id);
CREATE INDEX IF NOT EXISTS idx_billing_entries_staff_id        ON billing_entries (staff_id);
CREATE INDEX IF NOT EXISTS idx_billing_entries_cycle_id        ON billing_entries (billing_cycle_id);
CREATE INDEX IF NOT EXISTS idx_billing_entries_unbilled        ON billing_entries (matter_id, billed)
    WHERE billed = FALSE;   -- partial index — the hot path for balance calculation
CREATE INDEX IF NOT EXISTS idx_billing_entries_entry_date      ON billing_entries (matter_id, entry_date DESC);

-- ── trust_ledger ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_trust_ledger_matter_id ON trust_ledger (matter_id);
CREATE INDEX IF NOT EXISTS idx_trust_ledger_date      ON trust_ledger (matter_id, transaction_date);

-- ── fee_agreements ───────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_fee_agreements_matter_id ON fee_agreements (matter_id);
CREATE INDEX IF NOT EXISTS idx_fee_agreements_status    ON fee_agreements (matter_id, status);

-- ── matter_events ────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_matter_events_matter_id  ON matter_events (matter_id);
CREATE INDEX IF NOT EXISTS idx_matter_events_event_date ON matter_events (matter_id, event_date ASC);

-- ── discovery_requests ───────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_discovery_requests_matter_id ON discovery_requests (matter_id);
CREATE INDEX IF NOT EXISTS idx_discovery_requests_status    ON discovery_requests (matter_id, status);
CREATE INDEX IF NOT EXISTS idx_discovery_requests_type      ON discovery_requests (matter_id, request_type);

-- ── discovery_responses ──────────────────────────────────────────────────────
-- UNIQUE on discovery_request_id already covers the primary lookup.
CREATE INDEX IF NOT EXISTS idx_discovery_responses_is_final
    ON discovery_responses (discovery_request_id, is_final);

-- ── user_roles ───────────────────────────────────────────────────────────────
-- UNIQUE on supabase_uid already created by constraint.
CREATE INDEX IF NOT EXISTS idx_user_roles_staff_id  ON user_roles (staff_id)  WHERE staff_id  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_roles_client_id ON user_roles (client_id) WHERE client_id IS NOT NULL;

-- ── audit_log ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_audit_log_entity     ON audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_uid        ON audit_log (supabase_uid);
CREATE INDEX IF NOT EXISTS idx_audit_log_action     ON audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC);


-- =============================================================================
-- SECTION 9: TRIGRAM INDEXES FOR CONFLICT SEARCH
-- These support the search_conflicts() function in 004_functions.sql.
-- Requires pg_trgm (001_extensions.sql).
-- =============================================================================

-- name_to_text() is defined in 004_functions.sql; run that file first if
-- creating indexes independently, or use the combined runner script.

CREATE INDEX IF NOT EXISTS idx_clients_name_trgm
    ON clients USING gin (
        (
            lower(
                trim(
                    (name->>'first_name') || ' ' || (name->>'last_name')
                )
            )
        ) gin_trgm_ops
    );

CREATE INDEX IF NOT EXISTS idx_opposing_parties_name_trgm
    ON opposing_parties USING gin (lower(full_name) gin_trgm_ops);

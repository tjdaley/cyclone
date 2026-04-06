-- =============================================================================
-- 005_rls.sql
-- Row-Level Security policies.
--
-- IMPORTANT: The FastAPI backend uses the SERVICE ROLE KEY which bypasses
-- RLS entirely. These policies are a security backstop for any direct
-- Supabase client access (e.g. anon key from a compromised frontend).
-- Primary access control is enforced at the FastAPI route layer via
-- require_role() in app/dependencies.py.
--
-- Run after 004_functions.sql.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Enable RLS on all tables
-- ---------------------------------------------------------------------------
ALTER TABLE offices              ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff                ENABLE ROW LEVEL SECURITY;
ALTER TABLE clients              ENABLE ROW LEVEL SECURITY;
ALTER TABLE matters              ENABLE ROW LEVEL SECURITY;
ALTER TABLE matter_staff         ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_splits       ENABLE ROW LEVEL SECURITY;
ALTER TABLE opposing_parties     ENABLE ROW LEVEL SECURITY;
ALTER TABLE matter_rate_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_cycles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_entries      ENABLE ROW LEVEL SECURITY;
ALTER TABLE trust_ledger         ENABLE ROW LEVEL SECURITY;
ALTER TABLE fee_agreements       ENABLE ROW LEVEL SECURITY;
ALTER TABLE matter_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_requests   ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_responses  ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles           ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log            ENABLE ROW LEVEL SECURITY;


-- ---------------------------------------------------------------------------
-- Helper function: resolve role from user_roles for the calling Supabase user.
-- Returns NULL if the user has no role assignment.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION auth_role()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER AS $$
    SELECT role FROM user_roles WHERE supabase_uid = auth.uid()::TEXT;
$$;

-- Helper: return the client_id for the calling client user (NULL for staff).
CREATE OR REPLACE FUNCTION auth_client_id()
RETURNS INTEGER
LANGUAGE sql
STABLE
SECURITY DEFINER AS $$
    SELECT client_id FROM user_roles WHERE supabase_uid = auth.uid()::TEXT;
$$;

-- Helper: return the staff_id for the calling staff user (NULL for clients).
CREATE OR REPLACE FUNCTION auth_staff_id()
RETURNS INTEGER
LANGUAGE sql
STABLE
SECURITY DEFINER AS $$
    SELECT staff_id FROM user_roles WHERE supabase_uid = auth.uid()::TEXT;
$$;


-- =============================================================================
-- OFFICES — staff read-only; admin full access
-- =============================================================================
CREATE POLICY offices_staff_select ON offices
    FOR SELECT USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY offices_admin_all ON offices
    FOR ALL USING (auth_role() = 'admin');


-- =============================================================================
-- STAFF — all staff can view; only admins can modify
-- =============================================================================
CREATE POLICY staff_staff_select ON staff
    FOR SELECT USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY staff_admin_all ON staff
    FOR ALL USING (auth_role() = 'admin');


-- =============================================================================
-- CLIENTS
--   Staff: full access to all clients.
--   Clients: read their own record only.
-- =============================================================================
CREATE POLICY clients_staff_all ON clients
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY clients_client_self ON clients
    FOR SELECT USING (id = auth_client_id());


-- =============================================================================
-- MATTERS
--   Staff: full access to all matters.
--   Clients: read their own matters (where client_id matches).
-- =============================================================================
CREATE POLICY matters_staff_all ON matters
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY matters_client_own ON matters
    FOR SELECT USING (client_id = auth_client_id());


-- =============================================================================
-- MATTER_STAFF — staff read; admins/attorneys write
-- =============================================================================
CREATE POLICY matter_staff_staff_select ON matter_staff
    FOR SELECT USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY matter_staff_attorney_write ON matter_staff
    FOR ALL USING (auth_role() IN ('attorney', 'admin'));


-- =============================================================================
-- BILLING_SPLITS — staff read/write; no client access
-- =============================================================================
CREATE POLICY billing_splits_staff_all ON billing_splits
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));


-- =============================================================================
-- OPPOSING_PARTIES — staff full access; no client access
-- =============================================================================
CREATE POLICY opposing_parties_staff_all ON opposing_parties
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));


-- =============================================================================
-- MATTER_RATE_OVERRIDES — admin only (contains rate information)
-- =============================================================================
CREATE POLICY rate_overrides_admin_all ON matter_rate_overrides
    FOR ALL USING (auth_role() = 'admin');

CREATE POLICY rate_overrides_attorney_select ON matter_rate_overrides
    FOR SELECT USING (auth_role() IN ('attorney', 'paralegal'));


-- =============================================================================
-- BILLING_CYCLES
--   Staff: full access.
--   Clients: read closed cycles for their own matters only.
-- =============================================================================
CREATE POLICY billing_cycles_staff_all ON billing_cycles
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY billing_cycles_client_closed ON billing_cycles
    FOR SELECT USING (
        auth_role() = 'client'
        AND status = 'closed'
        AND matter_id IN (
            SELECT id FROM matters WHERE client_id = auth_client_id()
        )
    );


-- =============================================================================
-- BILLING_ENTRIES
--   Staff: full access.
--   Clients: read billable entries for their matters (after billing cycle closed).
-- =============================================================================
CREATE POLICY billing_entries_staff_all ON billing_entries
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY billing_entries_client_billed ON billing_entries
    FOR SELECT USING (
        auth_role() = 'client'
        AND billed = TRUE
        AND billable = TRUE
        AND matter_id IN (
            SELECT id FROM matters WHERE client_id = auth_client_id()
        )
    );


-- =============================================================================
-- TRUST_LEDGER — staff read; admins/attorneys post; no client access
-- =============================================================================
CREATE POLICY trust_ledger_attorney_all ON trust_ledger
    FOR ALL USING (auth_role() IN ('attorney', 'admin'));

CREATE POLICY trust_ledger_paralegal_select ON trust_ledger
    FOR SELECT USING (auth_role() = 'paralegal');


-- =============================================================================
-- FEE_AGREEMENTS
--   Staff: full access.
--   Clients: read their own executed agreements.
-- =============================================================================
CREATE POLICY fee_agreements_staff_all ON fee_agreements
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY fee_agreements_client_own ON fee_agreements
    FOR SELECT USING (
        auth_role() = 'client'
        AND matter_id IN (
            SELECT id FROM matters WHERE client_id = auth_client_id()
        )
    );


-- =============================================================================
-- MATTER_EVENTS
--   Staff: full access.
--   Clients: read events for their own matters.
-- =============================================================================
CREATE POLICY matter_events_staff_all ON matter_events
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY matter_events_client_own ON matter_events
    FOR SELECT USING (
        auth_role() = 'client'
        AND matter_id IN (
            SELECT id FROM matters WHERE client_id = auth_client_id()
        )
    );


-- =============================================================================
-- DISCOVERY_REQUESTS
--   Staff: full access.
--   Clients: read their own matter's requests.
-- =============================================================================
CREATE POLICY discovery_requests_staff_all ON discovery_requests
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY discovery_requests_client_own ON discovery_requests
    FOR SELECT USING (
        auth_role() = 'client'
        AND matter_id IN (
            SELECT id FROM matters WHERE client_id = auth_client_id()
        )
    );


-- =============================================================================
-- DISCOVERY_RESPONSES
--   Staff: full access.
--   Clients: read + update their own responses (not is_final, not attorney fields).
-- =============================================================================
CREATE POLICY discovery_responses_staff_all ON discovery_responses
    FOR ALL USING (auth_role() IN ('attorney', 'paralegal', 'admin'));

CREATE POLICY discovery_responses_client_select ON discovery_responses
    FOR SELECT USING (
        auth_role() = 'client'
        AND discovery_request_id IN (
            SELECT dr.id FROM discovery_requests dr
            JOIN matters m ON m.id = dr.matter_id
            WHERE m.client_id = auth_client_id()
        )
    );

-- Clients may update only their own draft response fields (not attorney fields)
CREATE POLICY discovery_responses_client_update ON discovery_responses
    FOR UPDATE USING (
        auth_role() = 'client'
        AND is_final = FALSE
        AND discovery_request_id IN (
            SELECT dr.id FROM discovery_requests dr
            JOIN matters m ON m.id = dr.matter_id
            WHERE m.client_id = auth_client_id()
        )
    );


-- =============================================================================
-- USER_ROLES — users see their own row; admins see all; no writes via anon key
-- =============================================================================
CREATE POLICY user_roles_self_select ON user_roles
    FOR SELECT USING (supabase_uid = auth.uid()::TEXT);

CREATE POLICY user_roles_admin_all ON user_roles
    FOR ALL USING (auth_role() = 'admin');


-- =============================================================================
-- AUDIT_LOG — read-only for admins; no writes via anon key (backend only)
-- =============================================================================
CREATE POLICY audit_log_admin_select ON audit_log
    FOR SELECT USING (auth_role() = 'admin');

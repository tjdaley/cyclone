-- =============================================================================
-- 004_functions.sql
-- Business-logic stored functions and views.
-- Run after 003_indexes_triggers.sql.
-- =============================================================================


-- =============================================================================
-- SECTION 1: NAME HELPERS
-- =============================================================================

-- ---------------------------------------------------------------------------
-- name_to_text(JSONB) → TEXT
-- Extracts a searchable "first last" string from a FullName JSONB object.
-- IMMUTABLE so it can be used in functional indexes (see 003_indexes).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION name_to_text(name_json JSONB)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT AS $$
    SELECT lower(
        trim(
            coalesce(name_json->>'first_name', '') || ' ' ||
            coalesce(name_json->>'last_name',  '')
        )
    );
$$;


-- =============================================================================
-- SECTION 2: CONFLICT-OF-INTEREST SEARCH
-- Uses pg_trgm similarity. Called by ConflictService as Phase 2 upgrade
-- over the Python substring scan. Invoke via supabase.rpc('search_conflicts').
-- =============================================================================

-- ---------------------------------------------------------------------------
-- search_conflicts(search_name TEXT, threshold FLOAT) → TABLE
-- Returns all clients and opposing parties whose name is similar to the
-- input, ordered by descending similarity score.
--
-- Parameters:
--   search_name  TEXT   — Full name of the prospective client or opposing party.
--   threshold    FLOAT  — Minimum similarity score (0.0–1.0). Default 0.30.
--
-- Returns rows: source TEXT, entity_id INT, matched_name TEXT, sim FLOAT
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_conflicts(
    search_name TEXT,
    threshold   FLOAT DEFAULT 0.30
)
RETURNS TABLE (
    source       TEXT,
    entity_id    INTEGER,
    matched_name TEXT,
    sim          FLOAT
)
LANGUAGE sql
STABLE AS $$
    -- Search existing clients
    SELECT
        'client'::TEXT                                AS source,
        c.id                                          AS entity_id,
        name_to_text(c.name)                          AS matched_name,
        similarity(name_to_text(c.name), lower(search_name))::FLOAT AS sim
    FROM clients c
    WHERE similarity(name_to_text(c.name), lower(search_name)) > threshold

    UNION ALL

    -- Search opposing parties on all matters
    SELECT
        'opposing_party'::TEXT                        AS source,
        op.id                                         AS entity_id,
        lower(op.full_name)                           AS matched_name,
        similarity(lower(op.full_name), lower(search_name))::FLOAT AS sim
    FROM opposing_parties op
    WHERE similarity(lower(op.full_name), lower(search_name)) > threshold

    ORDER BY sim DESC;
$$;

-- ---------------------------------------------------------------------------
-- search_conflicts_multi(names TEXT[], threshold FLOAT) → TABLE
-- Convenience wrapper: runs search_conflicts for each name in the array
-- and returns all hits deduplicated by (source, entity_id).
-- Useful when checking both a client name and multiple opposing parties
-- in one RPC call.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION search_conflicts_multi(
    names     TEXT[],
    threshold FLOAT DEFAULT 0.30
)
RETURNS TABLE (
    source       TEXT,
    entity_id    INTEGER,
    matched_name TEXT,
    max_sim      FLOAT,
    matched_input TEXT
)
LANGUAGE plpgsql
STABLE AS $$
DECLARE
    n TEXT;
BEGIN
    CREATE TEMP TABLE _conflict_hits (
        source       TEXT,
        entity_id    INTEGER,
        matched_name TEXT,
        sim          FLOAT,
        matched_input TEXT
    ) ON COMMIT DROP;

    FOREACH n IN ARRAY names LOOP
        INSERT INTO _conflict_hits
        SELECT sc.source, sc.entity_id, sc.matched_name, sc.sim, n
        FROM search_conflicts(n, threshold) sc;
    END LOOP;

    RETURN QUERY
    SELECT
        h.source,
        h.entity_id,
        h.matched_name,
        max(h.sim)::FLOAT AS max_sim,
        (array_agg(h.matched_input ORDER BY h.sim DESC))[1] AS matched_input
    FROM _conflict_hits h
    GROUP BY h.source, h.entity_id, h.matched_name
    ORDER BY max_sim DESC;
END;
$$;


-- =============================================================================
-- SECTION 3: CLIENT FINANCIAL BALANCE
-- Matches the formula in BillingService.get_client_balance().
-- Invoke via supabase.rpc('get_matter_balance', {'p_matter_id': 42}).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- get_matter_balance(p_matter_id INTEGER) → TABLE
-- Returns the trust balance, unbilled total, net balance, and status string
-- ('green' | 'yellow' | 'red') for a matter.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_matter_balance(p_matter_id INTEGER)
RETURNS TABLE (
    trust_balance  NUMERIC,
    unbilled_total NUMERIC,
    balance        NUMERIC,
    status         TEXT
)
LANGUAGE plpgsql
STABLE AS $$
DECLARE
    v_trust_balance  NUMERIC;
    v_unbilled_total NUMERIC;
    v_balance        NUMERIC;
    v_retainer       NUMERIC;
    v_refresh_pct    NUMERIC;
    v_threshold      NUMERIC;
    v_status         TEXT;
BEGIN
    -- Get matter configuration
    SELECT m.retainer_amount, m.refresh_trigger_pct
    INTO   v_retainer, v_refresh_pct
    FROM   matters m
    WHERE  m.id = p_matter_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Matter not found: id=%', p_matter_id;
    END IF;

    -- Trust balance: deposits are positive, withdrawals and refunds negative
    SELECT COALESCE(
        SUM(CASE WHEN tl.transaction_type = 'deposit' THEN tl.amount ELSE -tl.amount END),
        0.00
    )
    INTO v_trust_balance
    FROM trust_ledger tl
    WHERE tl.matter_id = p_matter_id;

    -- Unbilled billable entries only
    SELECT COALESCE(
        SUM(
            CASE
                WHEN be.amount IS NOT NULL                          THEN be.amount
                WHEN be.hours IS NOT NULL AND be.rate IS NOT NULL   THEN be.hours * be.rate
                ELSE 0.00
            END
        ),
        0.00
    )
    INTO v_unbilled_total
    FROM billing_entries be
    WHERE be.matter_id = p_matter_id
      AND be.billed    = FALSE
      AND be.billable  = TRUE;

    v_balance   := v_trust_balance - v_unbilled_total;
    v_threshold := v_retainer * v_refresh_pct;

    IF v_balance < 0 THEN
        v_status := 'red';
    ELSIF v_balance < v_threshold THEN
        v_status := 'yellow';
    ELSE
        v_status := 'green';
    END IF;

    RETURN QUERY SELECT
        round(v_trust_balance,  2) AS trust_balance,
        round(v_unbilled_total, 2) AS unbilled_total,
        round(v_balance,        2) AS balance,
        v_status                   AS status;
END;
$$;


-- =============================================================================
-- SECTION 4: BILLING RATE RESOLUTION
-- Mirrors BillingService.resolve_rate() for use in reporting queries.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- resolve_billing_rate(p_matter_id INT, p_staff_id INT) → NUMERIC
-- Resolution order:
--   1. matter_rate_overrides for this (matter, staff) pair
--   2. Matter.rate_card->>'<role>'
--   3. StaffMember.default_billing_rate
-- Returns NULL if no rate found (admin with no rate configured).
-- Returns 0.00 if matter is pro bono.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION resolve_billing_rate(
    p_matter_id INTEGER,
    p_staff_id  INTEGER
)
RETURNS NUMERIC
LANGUAGE plpgsql
STABLE AS $$
DECLARE
    v_is_pro_bono BOOLEAN;
    v_rate        NUMERIC;
    v_role        TEXT;
    v_rate_card   JSONB;
BEGIN
    -- Check pro bono
    SELECT is_pro_bono, rate_card
    INTO   v_is_pro_bono, v_rate_card
    FROM   matters WHERE id = p_matter_id;

    IF v_is_pro_bono THEN
        RETURN 0.00;
    END IF;

    -- 1. Per-matter staff override
    SELECT rate INTO v_rate
    FROM matter_rate_overrides
    WHERE matter_id = p_matter_id AND staff_id = p_staff_id;

    IF FOUND THEN
        RETURN v_rate;
    END IF;

    -- 2. Rate card by role
    SELECT s.role INTO v_role FROM staff s WHERE s.id = p_staff_id;

    v_rate := (v_rate_card ->> v_role)::NUMERIC;
    IF v_rate IS NOT NULL THEN
        RETURN v_rate;
    END IF;

    -- 3. Staff default
    SELECT default_billing_rate INTO v_rate FROM staff WHERE id = p_staff_id;
    RETURN v_rate;  -- may be NULL for admins
END;
$$;


-- =============================================================================
-- SECTION 5: USEFUL VIEWS
-- =============================================================================

-- ---------------------------------------------------------------------------
-- v_matter_summary — one row per matter with denormalized client name,
-- current status, trust balance, and unbilled total.
-- Used by the staff portal matter list.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_matter_summary AS
SELECT
    m.id                                    AS matter_id,
    m.matter_name,
    m.matter_type,
    m.status,
    m.is_pro_bono,
    m.client_id,
    name_to_text(c.name)                    AS client_name,
    c.email                                 AS client_email,
    bal.trust_balance,
    bal.unbilled_total,
    bal.balance,
    bal.status                              AS balance_status,
    m.retainer_amount,
    m.refresh_trigger_pct,
    m.created_at
FROM matters m
JOIN clients c ON c.id = m.client_id
LEFT JOIN LATERAL get_matter_balance(m.id) bal ON TRUE;

-- ---------------------------------------------------------------------------
-- v_unbilled_entries — all unbilled, billable entries with resolved rate.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_unbilled_entries AS
SELECT
    be.id             AS entry_id,
    be.matter_id,
    m.matter_name,
    be.staff_id,
    name_to_text(s.name)  AS staff_name,
    s.role            AS staff_role,
    be.entry_type,
    be.entry_date,
    be.hours,
    be.rate,
    be.amount,
    COALESCE(
        be.amount,
        CASE WHEN be.hours IS NOT NULL AND be.rate IS NOT NULL
             THEN be.hours * be.rate END
    )                 AS computed_amount,
    be.description,
    be.billable,
    be.created_at
FROM billing_entries be
JOIN matters m ON m.id = be.matter_id
JOIN staff   s ON s.id = be.staff_id
WHERE be.billed   = FALSE
  AND be.billable = TRUE;

-- ---------------------------------------------------------------------------
-- v_discovery_progress — per-matter summary of discovery request statuses.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_discovery_progress AS
SELECT
    dr.matter_id,
    m.matter_name,
    dr.request_type,
    count(*)                                      FILTER (WHERE dr.status = 'pending_client')  AS pending_client,
    count(*)                                      FILTER (WHERE dr.status = 'pending_review')  AS pending_review,
    count(*)                                      FILTER (WHERE dr.status = 'finalized')       AS finalized,
    count(*)                                      FILTER (WHERE dr.status = 'objected')        AS objected,
    count(*)                                                                                    AS total
FROM discovery_requests dr
JOIN matters m ON m.id = dr.matter_id
GROUP BY dr.matter_id, m.matter_name, dr.request_type;

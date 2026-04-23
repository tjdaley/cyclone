-- 009_pleadings_and_oc.sql
--
-- Adds pleading ingestion infrastructure:
--   1. discovery_level column on matters
--   2. matter_children table (with needs_support_after_majority flag)
--   3. opposing_counsel table (dedup by state + bar number)
--   4. matter_opposing_counsel intersection table
--   5. matter_pleadings table (with storage_path and amendment chain)
--   6. matter_claims table (claims/defenses/affirmative defenses/counterclaims)
--   7. storage_path column on discovery_requests (retroactive)

BEGIN;

-- 1. Discovery level on matters
ALTER TABLE matters
    ADD COLUMN IF NOT EXISTS discovery_level TEXT
        CHECK (discovery_level IN ('level_1', 'level_2', 'level_3'));

-- 2. Children of the marriage/relationship
CREATE TABLE IF NOT EXISTS matter_children (
    id                              SERIAL      PRIMARY KEY,
    matter_id                       INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    name                            JSONB       NOT NULL,
    date_of_birth                   DATE        NOT NULL,
    sex                             TEXT        NOT NULL CHECK (sex IN ('male', 'female', 'other')),
    needs_support_after_majority    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_matter_children_matter_id ON matter_children (matter_id);

-- 3. Opposing counsel — one row per real-world attorney
CREATE TABLE IF NOT EXISTS opposing_counsel (
    id               SERIAL      PRIMARY KEY,
    name             JSONB       NOT NULL,
    firm_name        TEXT,
    street_address   TEXT,
    street_address_2 TEXT,
    city             TEXT,
    state            TEXT,
    postal_code      TEXT,
    email            TEXT,
    cell_phone       TEXT,
    telephone        TEXT,
    fax              TEXT,
    bar_state        TEXT        NOT NULL,
    bar_number       TEXT        NOT NULL,
    email_ccs        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ,
    UNIQUE (bar_state, bar_number)
);

-- 4. Matter ↔ opposing counsel intersection
CREATE TABLE IF NOT EXISTS matter_opposing_counsel (
    id                   SERIAL      PRIMARY KEY,
    matter_id            INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    opposing_counsel_id  INTEGER     NOT NULL REFERENCES opposing_counsel (id) ON DELETE CASCADE,
    opposing_party_id    INTEGER     REFERENCES opposing_parties (id) ON DELETE SET NULL,
    role                 TEXT        NOT NULL DEFAULT 'lead'
                             CHECK (role IN ('lead', 'co_counsel', 'local_counsel')),
    started_date         DATE,
    ended_date           DATE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ,
    UNIQUE (matter_id, opposing_counsel_id, opposing_party_id)
);

CREATE INDEX IF NOT EXISTS idx_matter_oc_matter_id ON matter_opposing_counsel (matter_id);
CREATE INDEX IF NOT EXISTS idx_matter_oc_counsel_id ON matter_opposing_counsel (opposing_counsel_id);

-- 5. Matter pleadings
CREATE TABLE IF NOT EXISTS matter_pleadings (
    id                   SERIAL      PRIMARY KEY,
    matter_id            INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    opposing_party_id    INTEGER     REFERENCES opposing_parties (id) ON DELETE SET NULL,
    title                TEXT        NOT NULL,
    filed_date           DATE,
    served_date          DATE,
    amends_pleading_id   INTEGER     REFERENCES matter_pleadings (id) ON DELETE SET NULL,
    is_supplement        BOOLEAN     NOT NULL DEFAULT FALSE,
    storage_path         TEXT,
    raw_text             TEXT,
    ingested_by_staff_id INTEGER     NOT NULL REFERENCES staff (id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_matter_pleadings_matter_id ON matter_pleadings (matter_id);
CREATE INDEX IF NOT EXISTS idx_matter_pleadings_amends ON matter_pleadings (amends_pleading_id);

-- 6. Matter claims / defenses / affirmative defenses / counterclaims
CREATE TABLE IF NOT EXISTS matter_claims (
    id                  SERIAL      PRIMARY KEY,
    matter_pleading_id  INTEGER     NOT NULL REFERENCES matter_pleadings (id) ON DELETE CASCADE,
    matter_id           INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    opposing_party_id   INTEGER     REFERENCES opposing_parties (id) ON DELETE SET NULL,
    kind                TEXT        NOT NULL
                            CHECK (kind IN ('claim', 'defense', 'affirmative_defense', 'counterclaim')),
    label               TEXT        NOT NULL,
    narrative           TEXT        NOT NULL,
    statute_rule_cited  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_matter_claims_matter_id ON matter_claims (matter_id);
CREATE INDEX IF NOT EXISTS idx_matter_claims_pleading_id ON matter_claims (matter_pleading_id);

-- 7. Retrofit discovery_requests for PDF storage
ALTER TABLE discovery_requests
    ADD COLUMN IF NOT EXISTS storage_path TEXT;

-- 8. updated_at triggers on new tables
CREATE OR REPLACE TRIGGER trg_matter_children_updated_at
    BEFORE UPDATE ON matter_children
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_opposing_counsel_updated_at
    BEFORE UPDATE ON opposing_counsel
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_matter_oc_updated_at
    BEFORE UPDATE ON matter_opposing_counsel
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_matter_pleadings_updated_at
    BEFORE UPDATE ON matter_pleadings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE TRIGGER trg_matter_claims_updated_at
    BEFORE UPDATE ON matter_claims
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- Supabase Storage bucket setup (run separately in the Supabase dashboard
-- or via the storage API once):
--
--   Bucket name: matter-documents
--   Public: NO (signed URLs only)
--   File size limit: 50 MB
--   Allowed MIME types: application/pdf
-- ─────────────────────────────────────────────────────────────────────────────

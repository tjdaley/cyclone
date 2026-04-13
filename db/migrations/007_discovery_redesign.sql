-- 007_discovery_redesign.sql
--
-- Restructures discovery tables:
--   1. Renames discovery_requests → discovery_request_items (individual numbered requests)
--   2. Creates new discovery_requests table (the parent document that was served)
--   3. Adds new columns to discovery_request_items
--
-- Run this in a fresh dev environment AFTER truncating discovery_responses and
-- the old discovery_requests table. In production, backfill discovery_request_id
-- before uncommenting the NOT NULL constraint.

BEGIN;

-- Step 1: Rename the existing table
ALTER TABLE IF EXISTS discovery_requests RENAME TO discovery_request_items;

-- Step 2: Drop old constraints that reference the old table/column scope
ALTER TABLE discovery_request_items
    DROP CONSTRAINT IF EXISTS discovery_requests_matter_id_request_type_request_number_key;

-- Step 3: Create the new parent table (the served document)
CREATE TABLE IF NOT EXISTS discovery_requests (
    id                    SERIAL PRIMARY KEY,
    matter_id             INTEGER     NOT NULL REFERENCES matters (id) ON DELETE CASCADE,
    ingested_by_staff_id  INTEGER     NOT NULL REFERENCES staff (id),
    propounded_date       DATE        NOT NULL,
    due_date              DATE        NOT NULL,
    request_type          TEXT        NOT NULL
                              CHECK (request_type IN (
                                  'interrogatories', 'production', 'disclosures', 'admissions'
                              )),
    look_back_date        DATE,
    response_served_date  DATE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ
);

-- Step 4: Drop the old request_type column from items (type now lives on the parent document)
ALTER TABLE discovery_request_items
    DROP COLUMN IF EXISTS request_type;

-- Step 5: Add FK and new columns to the items table
ALTER TABLE discovery_request_items
    ADD COLUMN IF NOT EXISTS discovery_request_id  INTEGER REFERENCES discovery_requests (id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS interpretations        JSONB   NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS privileges             JSONB   NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS objections             JSONB   NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS client_response_needed BOOLEAN NOT NULL DEFAULT TRUE;

-- Step 6: Add UNIQUE constraint scoped to the parent document
ALTER TABLE discovery_request_items
    ADD CONSTRAINT discovery_request_items_doc_request_unique
        UNIQUE (discovery_request_id, request_number);

-- Step 7: Register updated_at trigger on the new parent table
CREATE OR REPLACE TRIGGER trg_discovery_requests_updated_at
    BEFORE UPDATE ON discovery_requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Step 8: Fix trigger name on the renamed items table
DROP TRIGGER IF EXISTS trg_discovery_requests_updated_at ON discovery_request_items;
CREATE OR REPLACE TRIGGER trg_discovery_request_items_updated_at
    BEFORE UPDATE ON discovery_request_items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Step 9: In a dev environment with no existing data, uncomment this:
-- ALTER TABLE discovery_request_items ALTER COLUMN discovery_request_id SET NOT NULL;

COMMIT;

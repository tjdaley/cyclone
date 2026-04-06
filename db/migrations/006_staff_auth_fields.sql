-- =============================================================================
-- 006_staff_auth_fields.sql
-- Adds auth_email to staff and makes supabase_uid nullable to support the
-- first-login correlation flow.
--
-- If you ran 002_tables.sql before this migration, apply this file.
-- If you haven't run 002_tables.sql yet, the column definitions there
-- will be updated — no need to run this file separately.
-- =============================================================================

-- supabase_uid is now set by the correlation flow on first login, not at
-- record creation time, so the NOT NULL constraint is removed.
ALTER TABLE staff
    ALTER COLUMN supabase_uid DROP NOT NULL,
    ALTER COLUMN supabase_uid SET DEFAULT NULL;

-- auth_email is the address the employee will use to sign in (Google /
-- magic link). It is distinct from the work email stored in the email column.
-- UNIQUE ensures one pending activation per login address.
ALTER TABLE staff
    ADD COLUMN IF NOT EXISTS auth_email TEXT UNIQUE;

-- Index for the correlation lookup: auth_email + supabase_uid IS NULL
CREATE INDEX IF NOT EXISTS idx_staff_auth_email
    ON staff (auth_email)
    WHERE supabase_uid IS NULL;

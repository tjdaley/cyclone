-- =============================================================================
-- run_all.sql
-- Convenience script: execute all Cyclone migrations in order.
-- Paste the entire contents of this file into the Supabase SQL Editor,
-- or run via psql:
--
--   psql "$DATABASE_URL" -f db/migrations/run_all.sql
--
-- Each migration is idempotent (CREATE ... IF NOT EXISTS / CREATE OR REPLACE).
-- Safe to re-run after partial failures.
-- =============================================================================

\echo '==> 001_extensions.sql'
\ir 001_extensions.sql

\echo '==> 002_tables.sql'
\ir 002_tables.sql

\echo '==> 003_indexes_triggers.sql'
\ir 003_indexes_triggers.sql

\echo '==> 004_functions.sql'
\ir 004_functions.sql

\echo '==> 005_rls.sql'
\ir 005_rls.sql

\echo '==> All migrations complete.'

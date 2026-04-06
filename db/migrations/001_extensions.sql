-- =============================================================================
-- 001_extensions.sql
-- Enable PostgreSQL extensions required by Cyclone.
-- Run this first, as superuser (Supabase SQL Editor has the required role).
-- =============================================================================

-- Trigram similarity for conflict-of-interest name matching (§3.1.2)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- UUID generation for audit_log.id
CREATE EXTENSION IF NOT EXISTS pgcrypto;

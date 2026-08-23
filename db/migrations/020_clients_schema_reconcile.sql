-- 020_clients_schema_reconcile.sql
-- Brings the migration chain back in line with the deployed clients table.
--
-- 002_tables.sql describes a clients table that no longer exists: production
-- has grown six columns that no migration creates. Nothing is broken in
-- production — the columns are there — but a database built from run_all.sql
-- comes up missing them, and every insert then fails with PGRST204 the way
-- referred_to_staff_id did (019). This closes that gap so a fresh environment
-- matches production.
--
-- Every statement is a no-op where the column already exists, so this is safe
-- to run against production and required for a fresh build.
--
-- Columns reconciled, from information_schema on the live database:
--   supabase_uid       text                     null
--   auth_email         text                 not null
--   ok_to_rehire       boolean              not null default true
--   ending_ar_balance  real                 not null default 0
--   referral_type      text                 not null
--   telegram_id        text                     null

-- ── Client portal identity ───────────────────────────────────────────────
-- Populated during first-login correlation, mirroring staff.supabase_uid.
alter table clients add column if not exists supabase_uid text;
alter table clients add column if not exists telegram_id  text;

-- ── Billing / relationship flags ─────────────────────────────────────────
alter table clients add column if not exists ok_to_rehire      boolean not null default true;
alter table clients add column if not exists ending_ar_balance real    not null default 0;

-- ── NOT NULL columns without a default ───────────────────────────────────
-- Added nullable and then constrained, so the script also works on a table
-- that already holds rows. On a fresh (empty) database the backfills match
-- nothing and the constraint applies immediately; in production the columns
-- already exist and every statement here is a no-op.
alter table clients add column if not exists auth_email text;
update clients set auth_email = email where auth_email is null;
alter table clients alter column auth_email set not null;

-- 'other' is a member of settings.referral_types, so a backfilled row stays
-- valid against the intake dropdown.
alter table clients add column if not exists referral_type text;
update clients set referral_type = 'other' where referral_type is null;
alter table clients alter column referral_type set not null;

-- The client portal will look clients up by auth identity, the same way
-- user_roles does for staff.
create index if not exists idx_clients_supabase_uid on clients (supabase_uid);
create index if not exists idx_clients_auth_email   on clients (auth_email);

notify pgrst, 'reload schema';

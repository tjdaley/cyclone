-- 040_enable_rls.sql
--
-- Close direct PostgREST access to every table and view in `public`.
--
-- RUN BY HAND. Safe to run on a live system and safe to re-run. VERIFY FIRST
-- with the query below -- it reads the catalog only, no client data.
--
-- ── VERIFIED ON PRODUCTION 2026-09-17 ─────────────────────────────────────────
--
-- Every TABLE already had RLS: it had been switched on in the Supabase
-- dashboard, so the migrations never recorded it. The live exposure was the
-- two VIEWS below. Step 1 is therefore a no-op on production and still
-- matters: a database rebuilt from these migrations alone would have the gap
-- described next, and this is now the file that records the fix.
--
-- A view's rls_enabled is ALWAYS false -- Postgres views carry no RLS flag of
-- their own -- so that column says nothing about a view. For a view, read
-- anon_can_select.
--
-- ── WHAT WAS WRONG IN THE MIGRATIONS ─────────────────────────────────────────
--
-- Only migration 005 ever enabled Row-Level Security, on the seventeen tables
-- that existed then. Every table created afterwards -- jobs (which stores the
-- full text of pleadings in `result`), financial_accounts, statements,
-- transactions, tags and categories, pleadings, claims, children, opposing
-- counsel, discovery documents, every CRM and KB table, firms, llm_calls -- was
-- created without it, and no migration anywhere revokes Supabase's default
-- grants.
--
-- Supabase grants the `anon` and `authenticated` roles access to tables in
-- `public` by default, and PostgREST serves them at /rest/v1/<table>. The anon
-- (publishable) key is compiled into the frontend bundle, which is public by
-- construction. So a table with RLS disabled is readable -- and writable -- by
-- anyone who opens the app and copies the key out of the JavaScript. Supabase's
-- own Security Advisor reports this as an ERROR ("RLS Disabled in Public").
--
-- The three views in 004 are worse in one respect: a view runs with its OWNER'S
-- privileges unless declared otherwise, so it bypasses RLS even on the tables
-- 005 does protect. v_matter_summary and v_unbilled_entries read clients,
-- matters and billing entries.
--
-- ── WHY THIS DOES NOT BREAK THE APP ──────────────────────────────────────────
--
-- The backend reaches the database only through SupabaseManager, which uses the
-- service-role key, and the service role BYPASSES RLS. The frontend never
-- queries a table: supabaseClient.ts is auth only (CLAUDE.md §14), and auth
-- lives in the `auth` schema, not `public`. Storage lives in `storage`. So
-- enabling RLS with no policy denies exactly the two roles nothing legitimate
-- uses. The policies 005 wrote for a future client portal are untouched.
--
-- ── VERIFY FIRST (Supabase SQL editor; catalog only, reads no client data) ───
--
--   select c.relname                                   as name,
--          case c.relkind when 'v' then 'view'
--                         when 'm' then 'materialized view'
--                         else 'table' end             as kind,
--          c.relrowsecurity                            as rls_enabled,
--          has_table_privilege('anon', c.oid, 'SELECT') as anon_can_select
--     from pg_class c
--     join pg_namespace n on n.oid = c.relnamespace
--    where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm')
--    order by anon_can_select desc, rls_enabled, name;
--
-- EXPOSED means: a table with rls_enabled = false and anon_can_select = true,
-- or ANY view or materialized view with anon_can_select = true. Run it again
-- after this migration: no table should show rls_enabled = false, and no view
-- should show anon_can_select = true.
--
-- ── WHAT IT DOES ─────────────────────────────────────────────────────────────
--
-- It finds the objects from the catalog rather than listing them, so it also
-- covers anything that exists in the deployed database without a migration --
-- which CLAUDE.md §15 says has happened.

begin;

-- 1. Every table in `public` without RLS gets it. No policy is added: the
--    service role bypasses RLS, and nothing else should read these tables.
do $$
declare
    t record;
begin
    for t in
        select c.relname
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public'
           and c.relkind in ('r', 'p')
           and not c.relrowsecurity
         order by c.relname
    loop
        execute format('alter table public.%I enable row level security', t.relname);
        raise notice 'RLS enabled: public.%', t.relname;
    end loop;
end $$;

-- 2. Views: make them run as the CALLER, so RLS applies through them, and take
--    them away from anon and authenticated outright. Both, because the first
--    needs Postgres 15 and the second works everywhere.
do $$
declare
    v record;
begin
    for v in
        select c.relname, c.relkind
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname = 'public'
           and c.relkind in ('v', 'm')
         order by c.relname
    loop
        if v.relkind = 'v' then
            begin
                execute format('alter view public.%I set (security_invoker = on)', v.relname);
            exception when others then
                raise notice 'security_invoker not supported for public.% (%); relying on revoke',
                             v.relname, sqlerrm;
            end;
        end if;
        execute format('revoke all on public.%I from anon, authenticated', v.relname);
        raise notice 'View closed to anon/authenticated: public.%', v.relname;
    end loop;
end $$;

commit;

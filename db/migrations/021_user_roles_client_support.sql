-- 021_user_roles_client_support.sql
-- Lets a client hold a role, and replaces an FK that cannot express the rule.
--
-- user_roles.supabase_uid currently REFERENCES staff(supabase_uid). But
-- user_roles.role permits 'client', and the table carries a client_id column
-- for exactly that case — so a client can never be given a role row: their
-- auth uid is not in staff. The client portal would fail on its first insert.
--
-- The FK cannot be fixed by repointing it, either. supabase_uid identifies a
-- row in auth.users, which is outside the schema we control, so the reference
-- was never enforceable in the direction that matters. What IS enforceable is
-- the rule the table actually embodies: a role row belongs to exactly one of
-- staff or client, and which one must agree with the role.
--
-- All five existing rows are staff roles with client_id null, so the new
-- constraint validates against current data.

alter table user_roles
    drop constraint if exists user_roles_supabase_uid_fkey;

-- staff_id / client_id are the real relationships and remain enforced as FKs.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'user_roles_subject_matches_role'
    ) then
        alter table user_roles
            add constraint user_roles_subject_matches_role check (
                (role = 'client'     and client_id is not null and staff_id is null)
                or
                (role in ('attorney', 'paralegal', 'admin')
                     and staff_id is not null and client_id is null)
            );
    end if;
end $$;

-- The login path is "user_roles WHERE supabase_uid = <jwt sub>" (§10), so this
-- is the hot index for every authenticated request.
create index if not exists idx_user_roles_supabase_uid on user_roles (supabase_uid);

-- Correlation looks rows up by email before a uid exists.
create index if not exists idx_user_roles_auth_email on user_roles (auth_email);

notify pgrst, 'reload schema';

-- 052_financial_guest_access.sql
--
-- A financial professional -- a tracing expert, a CPA -- given access to one or
-- more matters' Accounts, Transactions and Inventory, with the client's consent.
-- Design: docs/specs/financial-guest-access.md.
--
-- Three rules the schema carries rather than leaving to the code:
--   * A grant is per guest, per matter, and is only ever ACTIVE on a recorded
--     consent (guest_consents) -- the check below makes an active grant with no
--     activation time unrepresentable.
--   * The document ledger is not a foreign key to statements, because a
--     rejected statement is hard-deleted and the record of what an expert was
--     provided must outlive it.
--   * A tag link names exactly one actor, now one of three.
--
-- Run by hand, before deploying the code that reads these tables.

begin;

-- ── The person ────────────────────────────────────────────────────────────
create table if not exists public.guest_professionals (
    id            serial primary key,
    firm_id       integer not null default 1 references public.firms (id),
    full_name     text not null,
    email         text not null,
    organization  text,
    profession    text,
    is_active     boolean not null default true,
    created_by_staff_id integer references public.staff (id),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz
);
create unique index if not exists uq_guest_professionals_email
    on public.guest_professionals (firm_id, lower(email));

-- ── Their access to one matter ────────────────────────────────────────────
create table if not exists public.matter_guest_grants (
    id                    serial primary key,
    matter_id             integer not null references public.matters (id) on delete cascade,
    guest_id              integer not null references public.guest_professionals (id),
    designation           text not null check (designation in
                              ('testifying_expert', 'consulting_expert', 'non_expert')),
    purpose               text,
    status                text not null default 'pending_consent' check (status in
                              ('pending_consent', 'active', 'revoked')),
    expires_at            timestamptz not null,
    requested_by_staff_id integer references public.staff (id),
    requested_by          text,
    activated_at          timestamptz,
    revoked_at            timestamptz,
    revoked_by_staff_id   integer references public.staff (id),
    revoked_by            text,
    revoke_reason         text,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz,
    constraint matter_guest_grants_active_has_time
        check (status <> 'active' or activated_at is not null),
    constraint matter_guest_grants_revoked_has_actor
        check (status <> 'revoked' or (revoked_at is not null and revoked_by is not null))
);
-- One live grant per guest per matter; a revoked one may be followed by another.
create unique index if not exists uq_matter_guest_grants_live
    on public.matter_guest_grants (matter_id, guest_id) where status <> 'revoked';
create index if not exists idx_matter_guest_grants_guest
    on public.matter_guest_grants (guest_id, status);

-- ── The client's consent, as a staff member attested it ───────────────────
create table if not exists public.guest_consents (
    id                    serial primary key,
    grant_id              integer not null references public.matter_guest_grants (id) on delete cascade,
    method                text not null check (method in
                              ('phone', 'in_person', 'email', 'letter', 'other')),
    consent_date          date not null,
    note                  text,
    attested_by_staff_id  integer not null references public.staff (id),
    attested_by           text not null,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz
);
create index if not exists idx_guest_consents_grant on public.guest_consents (grant_id);

-- ── What the guest was provided, at the document level ────────────────────
create table if not exists public.guest_document_ledger (
    id                   serial primary key,
    grant_id             integer not null references public.matter_guest_grants (id) on delete cascade,
    statement_id         integer not null,   -- NOT a foreign key: it must outlive a rejection
    financial_account_id integer,
    account_label        text,
    period_start         date,
    period_end           date,
    source_filename      text,
    bates_first          text,
    bates_last           text,
    first_available_at   timestamptz not null default now(),
    removed_at           timestamptz,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz,
    unique (grant_id, statement_id)
);

-- ── Sign-ins: not for disclosure, for "was it used after we revoked it?" ──
create table if not exists public.guest_sign_ins (
    id          serial primary key,
    guest_id    integer not null references public.guest_professionals (id),
    signed_in_at timestamptz not null default now(),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz
);
create index if not exists idx_guest_sign_ins_guest on public.guest_sign_ins (guest_id, signed_in_at);

-- ── A role row can belong to a guest ──────────────────────────────────────
alter table public.user_roles
    add column if not exists guest_id integer references public.guest_professionals (id);

alter table public.user_roles drop constraint if exists user_roles_subject_matches_role;
alter table public.user_roles
    add constraint user_roles_subject_matches_role check (
        (role = 'client' and client_id is not null and staff_id is null and guest_id is null)
        or
        (role in ('attorney', 'paralegal', 'admin')
             and staff_id is not null and client_id is null and guest_id is null)
        or
        (role = 'financial_professional'
             and guest_id is not null and staff_id is null and client_id is null)
    );

-- user_roles.role may be guarded by a CHECK listing the roles; widen it if so.
do $$
declare
    c record;
begin
    for c in
        select conname from pg_constraint
         where conrelid = 'public.user_roles'::regclass
           and contype = 'c'
           and conname <> 'user_roles_subject_matches_role'
           and pg_get_constraintdef(oid) ilike '%role%'
           and pg_get_constraintdef(oid) ilike '%paralegal%'
    loop
        execute format('alter table public.user_roles drop constraint %I', c.conname);
    end loop;
end $$;
alter table public.user_roles
    add constraint user_roles_role_check
    check (role in ('client', 'attorney', 'paralegal', 'admin', 'financial_professional'));

-- ── Guest tags ────────────────────────────────────────────────────────────
alter table public.transaction_tags
    add column if not exists guest_id integer references public.guest_professionals (id);
create index if not exists idx_transaction_tags_guest
    on public.transaction_tags (matter_id, guest_id) where guest_id is not null;

alter table public.financial_account_transaction_tags
    add column if not exists tagged_by_guest_id integer references public.guest_professionals (id);

alter table public.financial_account_transaction_tags
    drop constraint if exists financial_account_transaction_tags_source;
alter table public.financial_account_transaction_tags
    add constraint financial_account_transaction_tags_source
    check (source in ('staff', 'client', 'guest'));

alter table public.financial_account_transaction_tags
    drop constraint if exists financial_account_transaction_tags_one_actor;
alter table public.financial_account_transaction_tags
    add constraint financial_account_transaction_tags_one_actor
    check (
        (source = 'staff'  and tagged_by_staff_id is not null
                           and tagged_by_client_id is null and tagged_by_guest_id is null)
        or
        (source = 'client' and tagged_by_client_id is not null
                           and tagged_by_staff_id is null and tagged_by_guest_id is null)
        or
        (source = 'guest'  and tagged_by_guest_id is not null
                           and tagged_by_staff_id is null and tagged_by_client_id is null)
    );

-- ── Every table in public needs RLS, or the public key reads it (040) ─────
alter table public.guest_professionals   enable row level security;
alter table public.matter_guest_grants   enable row level security;
alter table public.guest_consents        enable row level security;
alter table public.guest_document_ledger enable row level security;
alter table public.guest_sign_ins        enable row level security;
revoke all on public.guest_professionals, public.matter_guest_grants, public.guest_consents,
              public.guest_document_ledger, public.guest_sign_ins
    from anon, authenticated;

commit;

notify pgrst, 'reload schema';

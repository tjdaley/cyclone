-- 054_matter_dashboard.sql
--
-- Two things the matter dashboard needs.
--
-- matters.billing_id: the matter's number in the firm's external billing
-- system, so records can be correlated during a migration and a matter can be
-- found by the number staff already know. Text, because external ids carry
-- leading zeros and letters. Unique where filled in, ignoring case and spaces:
-- two matters claiming one billing id would correlate one of them wrongly.
--
-- staff_recent_matters: which matters each staff member opened, and when, so
-- the opening page lists what somebody was actually working on. Kept on the
-- server so it follows a person between machines. One row per staff member per
-- matter, refreshed on each visit.
--
-- RUN BY HAND, before deploying the code that reads it. Safe to run twice.

alter table public.matters
    add column if not exists billing_id text;

create unique index if not exists uq_matters_billing_id
    on public.matters (lower(trim(billing_id)))
    where billing_id is not null and trim(billing_id) <> '';

create table if not exists public.staff_recent_matters (
    id          serial primary key,
    staff_id    integer not null references public.staff (id) on delete cascade,
    matter_id   integer not null references public.matters (id) on delete cascade,
    viewed_at   timestamptz not null default now(),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz,
    constraint uq_staff_recent_matters unique (staff_id, matter_id)
);
create index if not exists idx_staff_recent_matters_staff
    on public.staff_recent_matters (staff_id, viewed_at desc);

-- Every table in public needs RLS, or the public key reads it (040).
alter table public.staff_recent_matters enable row level security;
revoke all on public.staff_recent_matters from anon, authenticated;

notify pgrst, 'reload schema';

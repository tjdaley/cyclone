-- 051_account_currency.sql
--
-- An account is held in a currency, and until now every figure was assumed to
-- be dollars. A statement in Pakistani rupees imported as-is reads
-- PKR 10,000,000 as $10,000,000 and makes the household millionaires -- on an
-- inventory, on an FIS, in an exhibit.
--
-- Two things:
--
--   1. financial_accounts.currency -- the ISO 4217 code the account is kept in.
--      Defaults to USD, so every existing account keeps the meaning it had.
--      Stored figures are NEVER converted: a statement's amounts are what the
--      institution printed, in the currency it printed them in, and that is the
--      evidence.
--
--   2. matter_currency_rates -- the rate a matter uses to put a foreign balance
--      into dollars on the INVENTORY, which is the one report that has to add
--      accounts together. One rate per currency per matter, entered by a person
--      with the date it is as of and where it came from, because a converted
--      figure on a schedule is only as good as the rate somebody can cite.
--
-- Run by hand, before deploying the code that reads these columns.

alter table public.financial_accounts
    add column if not exists currency text not null default 'USD';

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'financial_accounts_currency_check'
    ) then
        alter table public.financial_accounts
            add constraint financial_accounts_currency_check
            check (currency ~ '^[A-Z]{3}$');
    end if;
end $$;

comment on column public.financial_accounts.currency is
    'ISO 4217 code the account is kept in. Amounts are stored as printed, never converted';

create table if not exists public.matter_currency_rates (
    id                  bigserial primary key,
    matter_id           integer not null references public.matters(id) on delete cascade,
    currency            text not null check (currency ~ '^[A-Z]{3}$' and currency <> 'USD'),
    -- USD per ONE unit of the currency: PKR at 0.0036 means 1 PKR = $0.0036.
    -- Ten places, because a small unit's rate has its significant digits far
    -- to the right of the point.
    usd_per_unit        numeric(20, 10) not null check (usd_per_unit > 0),
    as_of               date not null,
    source              text,
    entered_by_staff_id integer,
    entered_by          text,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz,
    unique (matter_id, currency)
);

-- Every table in public needs RLS, or the public key reads it (040).
alter table public.matter_currency_rates enable row level security;
revoke all on public.matter_currency_rates from anon, authenticated;
revoke all on sequence public.matter_currency_rates_id_seq from anon, authenticated;

comment on table public.matter_currency_rates is
    'The rate a matter uses to show a foreign-currency balance in dollars on the inventory';

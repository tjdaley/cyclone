-- 050_inventory_exclusion.sql
--
-- An account can be kept on the inventory and left out of its totals.
--
-- A stale account, a closed one whose last statement is years old, or a third
-- party's account the estate holds no interest in still belongs on the list --
-- somebody reading the schedule should see it was considered -- but its last
-- printed balance should not move the assets, the liabilities or the net.
--
-- This is a JUDGMENT, so it carries who made it, when, and (optionally) why.
-- The name is stored beside the id because these records are read back years
-- later, when a staff id means nothing to anybody. Not a foreign key, for the
-- same reason as category_rule_id: the trail has to outlive the row it names.
--
-- Not the same thing as property_character = 'no_marital_interest' (045), which
-- takes an account off the inventory altogether. This keeps it listed.
--
-- Run by hand, before deploying the code that reads these columns: the startup
-- schema check will otherwise log an ERROR per missing column, and every
-- account insert will be rejected (PGRST204).

alter table public.financial_accounts
    add column if not exists excluded_from_inventory boolean not null default false,
    add column if not exists inventory_exclusion_reason text,
    add column if not exists inventory_exclusion_note text,
    add column if not exists inventory_excluded_by_staff_id integer,
    add column if not exists inventory_excluded_by text,
    add column if not exists inventory_excluded_at timestamptz;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'financial_accounts_inventory_exclusion_reason_check'
    ) then
        alter table public.financial_accounts
            add constraint financial_accounts_inventory_exclusion_reason_check
            check (inventory_exclusion_reason is null or inventory_exclusion_reason in (
                'stale',
                'no_ownership_interest',
                'stipulated_separate_other_party',
                'other'
            ));
    end if;
end $$;

comment on column public.financial_accounts.excluded_from_inventory is
    'Listed on the account inventory but left out of its totals. A person''s decision, never derived';
comment on column public.financial_accounts.inventory_exclusion_reason is
    'Why it was excluded: stale | no_ownership_interest | stipulated_separate_other_party | other. Null when nobody said';
comment on column public.financial_accounts.inventory_exclusion_note is
    'The explanation, when the reason is other';

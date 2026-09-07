-- 034_statement_boundary.sql
--
-- Whether a statement is the first of an account, the last, or neither.
--
-- A gap report is only useful if it can tell a hole from an edge. Without this,
-- every account looks like it should have statements running back to the start
-- of the look-back period and forward to today, so an account opened in March
-- 2021 reports fourteen missing months that never existed — and somebody either
-- files a motion to compel documents nobody has, or spends an afternoon proving
-- they do not exist. The same in reverse for an account that was closed.
--
-- Nobody can extract this. A statement does not say "this is the first one",
-- and an opening balance of zero is not proof — accounts are swept to zero all
-- the time. So it is a human judgment, recorded like the other judgments on
-- this data (ownership, property_character): defaulted to the safe value and
-- set by somebody who looked.
--
-- 'intermediate' is that safe value. It means "expect statements on both
-- sides", which is the assumption that produces a gap report erring toward
-- asking for too much rather than too little.
--
-- KNOWN LIMITATION: an account opened and closed inside one statement period
-- cannot be expressed — it is both ends at once. Rare enough to leave, and the
-- fix is a fourth value ('only') rather than a different shape, so nothing here
-- has to change to add it later.
--
-- Run after 033.

alter table financial_account_statements
    add column if not exists boundary text not null default 'intermediate';

alter table financial_account_statements
    drop constraint if exists financial_account_statements_boundary_check;

alter table financial_account_statements
    add constraint financial_account_statements_boundary_check check (
        boundary in ('opening', 'closing', 'intermediate')
    );

comment on column financial_account_statements.boundary is
    'opening = the first statement of the account, so nothing is missing before it. '
    'closing = the last, so nothing is missing after it. intermediate = neither, the '
    'default. Set by a person: a statement does not say which it is, and an opening '
    'balance of zero is not proof. Read by the compliance matrix to tell a gap in a '
    'production from the edge of an account''s life.';

-- The matrix reads every statement on a matter, ordered by the month each one
-- closes in. This is the index behind that.
create index if not exists idx_statements_matter_period_end
    on financial_account_statements (matter_id, period_end);

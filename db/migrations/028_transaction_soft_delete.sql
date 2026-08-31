-- ═══════════════════════════════════════════════════════════════════════════
-- 028_transaction_soft_delete.sql
--
-- A transaction can be dropped from a statement without being destroyed.
--
-- Statements and accounts are deleted outright, because the Bates-stamped PDF
-- in Storage is the original record and re-importing it is the undo. A single
-- line is different: dropping one changes whether its statement reconciles, so
-- the removal is an assertion about the document — "this line is not printed
-- there" — and an assertion that reaches an exhibit has to say who made it.
--
-- The legitimate reason to drop a line is that extraction invented it: a row
-- read twice, or a daily-balance entry read as a transaction. When that is what
-- happened, reconciliation gets BETTER after the deletion, because the line was
-- never in the printed total. If it gets worse, something real was removed —
-- which is exactly why the row stays recoverable.
--
-- These rows are swept by the matter-close workflow, when that is built.
--
-- Run after 027.
-- ═══════════════════════════════════════════════════════════════════════════

alter table financial_account_transactions
    add column if not exists deleted_at         timestamptz,
    add column if not exists deleted_by_staff_id integer references staff (id),
    add column if not exists deletion_reason    text;

-- Every read of a statement's or an account's lines filters on this, so the
-- index is partial: the deleted rows are the rare ones, and a partial index
-- keeps the common "not deleted" scan off them entirely.
create index if not exists idx_transactions_live
    on financial_account_transactions (statement_id, line_no)
    where deleted_at is null;

create index if not exists idx_transactions_deleted
    on financial_account_transactions (financial_account_id)
    where deleted_at is not null;

notify pgrst, 'reload schema';

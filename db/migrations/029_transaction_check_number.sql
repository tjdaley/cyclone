-- ═══════════════════════════════════════════════════════════════════════════
-- 029_transaction_check_number.sql
--
-- The check number a transaction was drawn on.
--
-- Checks are how money leaves an account without saying where it went. A card
-- purchase names the merchant; a check says "CHECK 2495" and nothing else, so
-- following it means going back to the image or asking for it in discovery.
-- Being able to pull every check on an account, by number, is the start of that
-- work — and not every bank prints the images, so the number is often all there
-- is to ask about.
--
-- Text, not an integer: numbers are printed with leading zeros and occasionally
-- a letter, and the value is for citing and matching, never arithmetic.
--
-- Run after 028.
-- ═══════════════════════════════════════════════════════════════════════════

alter table financial_account_transactions
    add column if not exists check_number text;

-- "Every check on this account", and "check 2495" — both are lookups a person
-- does while tracing money, so both get an index. Partial: most transactions
-- are not checks.
create index if not exists idx_transactions_check_number
    on financial_account_transactions (financial_account_id, check_number)
    where check_number is not null;

notify pgrst, 'reload schema';

-- 038_insurance_accounts.sql
--
-- Cash-value life insurance and annuities as account types.
--
-- RUN BY HAND, in numeric order, BEFORE deploying the code that uses them.
-- `run_all.sql` covers 001-005 only.
--
-- ── WHY THESE ARE NOT "other" ────────────────────────────────────────────────
--
-- The same argument 036 made for crypto, and it applies clause for clause. A
-- cash-value policy is not a brokerage account wearing different clothes:
--
--   * it divides differently -- characterization follows the source of the
--     premiums, not the titling
--   * it carries a beneficiary designation, which no brokerage account has and
--     which a decree usually has to address
--   * the DEATH BENEFIT is not divisible property while the CASH VALUE is, so
--     one document holds two figures that must never be added together
--
-- Buried under `other` none of that is filterable, and an inventory cannot find
-- the policies. An annuity is separated from life insurance for a reason of its
-- own: one in payout behaves like an income stream rather than an asset, and a
-- report that cannot tell them apart will either treat a pension-like annuity
-- as divisible property or miss a policy's cash value entirely.
--
-- ── WHAT CHANGES IN THE CODE ─────────────────────────────────────────────────
--
-- Both types join `_CASH_FLOW_ACCOUNTS` in statement_service, which means a
-- statement on one of them is read the way a brokerage statement is read:
-- sub-account transfers inside the policy are not extracted (nothing crossed
-- the account boundary, exactly as with a stock purchase), the balance is the
-- TOTAL policy value, change in value is the figure the statement PRINTS and is
-- never derived, and the statement is checked against its own summary block
-- rather than against bank-shaped deposit/withdrawal counts.
--
-- This was found on a real import: an Ameriprise/RiverSource VUL statement
-- (12/31/2022-03/31/2023) reconciled to the cent and was still flagged
-- INCOMPLETE_EXTRACTION, because the bank-shaped check read `premium_payments`
-- as the printed DEBIT total -- a premium payment is money IN on a policy and
-- money OUT on a bank statement.
--
-- ── SAFE TO RE-RUN ───────────────────────────────────────────────────────────
--
-- The constraint is located by shape rather than by name (036 may have left it
-- under either name) and rebuilt. Nothing is deleted and no row is rewritten,
-- so running this on a database that already has it is a no-op.

begin;

-- ── Widen the account_type CHECK ─────────────────────────────────────────────
--
-- Located the way 036 does it: by looking for the check constraint on this
-- table that mentions account_type, because the name has not been stable.

do $$
declare
    existing text;
begin
    select con.conname into existing
    from pg_constraint con
    join pg_class rel on rel.oid = con.conrelid
    where rel.relname = 'financial_accounts'
      and con.contype = 'c'
      and pg_get_constraintdef(con.oid) ilike '%account_type%'
    limit 1;

    if existing is not null then
        execute format('alter table financial_accounts drop constraint %I', existing);
    end if;
end $$;

alter table financial_accounts
    add constraint financial_accounts_account_type check (
        account_type in (
            'checking', 'savings', 'brokerage', 'crypto', 'credit_card',
            'retirement', 'hsa', 'loan', 'other',
            -- 038
            'life_insurance', 'annuity'
        )
    );

-- ── Nothing is migrated automatically, and that is deliberate ────────────────
--
-- A policy already imported is sitting under `other` or `brokerage`, and only a
-- person can say which rows are policies -- the institution name does not
-- settle it, since Ameriprise, Fidelity and Schwab all issue both brokerage
-- accounts and insurance products under one brand. An UPDATE guessing from the
-- institution would re-type real brokerage accounts and silently change how
-- their statements are read on the next import.
--
-- To find candidates for a human to review:
--
--   select a.id, a.institution, a.account_type, a.account_number_masked,
--          count(s.id) as statements
--     from financial_accounts a
--     left join financial_account_statements s on s.account_id = a.id
--    where a.account_type in ('other', 'brokerage')
--    group by a.id
--    order by a.institution;
--
-- Re-typing an account does NOT re-read its statements. Lines already extracted
-- stay as they were; only the next import follows the new path. Where that
-- matters, reject the statement and re-import it, which is the documented way
-- to re-read a document.

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- 026_account_ownership_and_merge.sql
--
-- Two changes to financial_accounts:
--
--   1. `ownership` — who holds the account. `opposing_party_id` alone cannot
--      say "joint", and joint is the case that decides how an asset divides.
--   2. ON UPDATE CASCADE on the transaction→statement composite FK, so a
--      statement can be moved to a different account.
--
-- Run after 025.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Ownership ────────────────────────────────────────────────────────────
-- Before this, ownership was inferred from opposing_party_id: null meant "our
-- client's", a value meant "theirs". That encoding has no way to say *joint*,
-- and a jointly held account is not a footnote — it is the difference between
-- an asset one side keeps and an asset the court divides.
--
-- opposing_party_id keeps its job of naming *which* other party, and now reads
-- as the co-holder on a joint account rather than the sole owner.
--
-- Defaults to 'unknown' rather than 'client_sole': every account already in the
-- table was created by extraction, which never determined this. Calling them
-- all the client's would be inventing a characterization nobody made.
alter table financial_accounts
    add column if not exists ownership text not null default 'unknown';

alter table financial_accounts
    drop constraint if exists financial_accounts_ownership_check;
alter table financial_accounts
    add constraint financial_accounts_ownership_check
        check (ownership in ('client_sole', 'opposing_sole', 'joint', 'third_party', 'unknown'));

create index if not exists idx_financial_accounts_ownership
    on financial_accounts (matter_id, ownership);

-- ── Moving a statement between accounts ──────────────────────────────────
-- Extraction cannot always read the institution — on many statements the name
-- is only in the letterhead graphic — so the same real account can land as two
-- rows, one of them "Unknown institution". Merging them means repointing the
-- statements.
--
-- That update is blocked as the schema stands. A transaction's composite FK
-- references (statement_id, financial_account_id) on the statement, so moving
-- the statement changes a key its children point at, and the default NO ACTION
-- refuses. Updating the children first fails just as hard, in the other
-- direction: there is no order that works.
--
-- ON UPDATE CASCADE resolves it, and is what the denormalized column meant all
-- along — financial_account_id on a transaction is a copy of its statement's,
-- so it should follow the statement automatically. ON DELETE CASCADE is
-- unchanged.
alter table financial_account_transactions
    drop constraint if exists financial_account_transactions_parent_fkey;
alter table financial_account_transactions
    add constraint financial_account_transactions_parent_fkey
        foreign key (statement_id, financial_account_id)
        references financial_account_statements (id, financial_account_id)
        on update cascade
        on delete cascade;

notify pgrst, 'reload schema';

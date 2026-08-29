-- 023_financial_statements.sql
-- Account statement ingestion: the spine of inventories, settlement
-- spreadsheets, and waste / reimbursement exhibits.
--
--   matters -> financial_accounts -> financial_account_statements
--                                 -> financial_account_transactions
--
-- Three things this schema is built around, because the output ends up in
-- front of a judge:
--
-- 1. SIGNED AMOUNTS, ONE CONVENTION. A transaction's amount is signed by how
--    it moves the balance the institution itself prints. A bank deposit is
--    positive and a withdrawal negative; a credit-card purchase is positive
--    (it increases the balance owed) and a payment negative. That gives every
--    account type the same arithmetic:
--        beginning_balance + sum(amount) = ending_balance
--
-- 2. EVERY STATEMENT CHECKS ITSELF. The sum above is computed on commit and
--    stored. When it does not tie, the delta is recorded and the statement is
--    marked unreconciled — nothing is invented to force it closed. A synthetic
--    balancing row is exactly what opposing counsel would cross-examine on.
--
-- 3. INFERENCE IS RECORDED, NOT HIDDEN. Statements print "03/24" with no year;
--    a merchant line mixes the payee and the city. Those derivations are real
--    and useful, but they are inferences, so each one leaves a flag naming the
--    field it touched. An exhibit can then footnote or exclude them.
--
-- Money is numeric(14,2) throughout. Never float: `real` cannot represent
-- cents exactly, and a rounding artifact in a trial exhibit is indefensible.

-- ── Accounts ─────────────────────────────────────────────────────────────
create table if not exists financial_accounts (
    id                    serial      primary key,
    matter_id             integer     not null references matters (id) on delete cascade,
    institution           text        not null,
    account_type          text        not null
                              check (account_type in (
                                  'checking', 'savings', 'brokerage', 'credit_card',
                                  'retirement', 'hsa', 'loan', 'other'
                              )),
    account_number_last4  text,
    account_number_masked text,
    name_on_account       text,
    -- Whose account this is. Null means our client's or not yet determined.
    opposing_party_id     integer     references opposing_parties (id) on delete set null,
    -- Characterization for the inventory. Deliberately nullable: character is
    -- argued, not extracted, and is often unresolved until trial.
    property_character    text
                              check (property_character is null or property_character in (
                                  'community', 'separate_petitioner',
                                  'separate_respondent', 'mixed', 'disputed'
                              )),
    purpose               text,       -- "Operating acct", "IRS money" — the client's own words
    notes                 text,
    is_closed             boolean     not null default false,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz,
    -- Lets statements carry a denormalized matter_id that cannot drift.
    constraint financial_accounts_id_matter_uniq unique (id, matter_id)
);

-- The dedup key when a statement names an institution and the last four
-- digits. Partial, because plenty of productions redact the number entirely.
create unique index if not exists idx_financial_accounts_dedup
    on financial_accounts (matter_id, lower(institution), account_number_last4)
    where account_number_last4 is not null;

create index if not exists idx_financial_accounts_matter on financial_accounts (matter_id);

-- ── Statements ───────────────────────────────────────────────────────────
create table if not exists financial_account_statements (
    id                       serial      primary key,
    financial_account_id     integer     not null,
    matter_id                integer     not null,
    period_start             date        not null,
    period_end               date        not null,
    beginning_balance        numeric(14,2),
    ending_balance           numeric(14,2),
    -- beginning_balance + sum(transactions.amount), computed on commit.
    computed_ending_balance  numeric(14,2),
    reconciled               boolean     not null default false,
    reconciliation_delta     numeric(14,2),
    -- Totals the statement itself prints (payments, purchases, fees, interest).
    -- A second, independent check on the transaction list.
    printed_totals           jsonb       not null default '{}'::jsonb,
    -- Statement-level findings: NO_ACCOUNT_MATCH, DUPLICATE_PERIOD, UNRECONCILED…
    flags                    jsonb       not null default '[]'::jsonb,
    review_status            text        not null default 'needs_review'
                                 check (review_status in (
                                     'auto_accepted', 'needs_review', 'accepted', 'rejected'
                                 )),
    storage_path             text,
    raw_text                 text,
    -- Provenance: which model, which job, which source file and page count.
    extraction               jsonb       not null default '{}'::jsonb,
    source_job_id            uuid        references jobs (id) on delete set null,
    ingested_by_staff_id     integer     not null references staff (id),
    created_at               timestamptz not null default now(),
    updated_at               timestamptz,

    -- matter_id is a shortcut for matter-level queries, held true by the
    -- composite key rather than by convention (same treatment as 022).
    constraint financial_account_statements_parent_fkey
        foreign key (financial_account_id, matter_id)
        references financial_accounts (id, matter_id) on delete cascade,
    -- Lets transactions carry a denormalized account id that cannot drift.
    constraint financial_account_statements_id_account_uniq
        unique (id, financial_account_id)
);

-- One statement per account per period. Rejected extractions are excluded so a
-- bad ingest can be thrown away and the document re-run.
create unique index if not exists idx_statements_account_period
    on financial_account_statements (financial_account_id, period_start, period_end)
    where review_status <> 'rejected';

-- Drives the exceptions queue.
create index if not exists idx_statements_review
    on financial_account_statements (matter_id, review_status, period_end desc);

-- ── Transactions ─────────────────────────────────────────────────────────
create table if not exists financial_account_transactions (
    id                   bigserial   primary key,
    statement_id         integer     not null,
    financial_account_id integer     not null,
    line_no              integer     not null,
    transaction_date     date,
    posted_date          date,
    date_provenance      text        not null default 'printed'
                             check (date_provenance in ('printed', 'derived', 'unknown')),
    description          text        not null,
    -- Statements wrap a merchant across lines; keeping the raw lines means the
    -- exhibit can quote the document verbatim.
    description_lines    jsonb       not null default '[]'::jsonb,
    counterparty         text,       -- normalized payee, for grouping in a waste exhibit
    location             text,
    amount               numeric(14,2) not null,
    running_balance      numeric(14,2),
    category             text,       -- classification for waste / reimbursement analysis
    flags                jsonb       not null default '[]'::jsonb,
    created_at           timestamptz not null default now(),

    constraint financial_account_transactions_parent_fkey
        foreign key (statement_id, financial_account_id)
        references financial_account_statements (id, financial_account_id) on delete cascade
);

create index if not exists idx_transactions_statement
    on financial_account_transactions (statement_id, line_no);

-- The query behind every waste and reimbursement exhibit: one account's
-- activity across all statements, in date order.
create index if not exists idx_transactions_account_date
    on financial_account_transactions (financial_account_id, transaction_date);

-- Grouping by payee across an account's history.
create index if not exists idx_transactions_counterparty
    on financial_account_transactions (financial_account_id, counterparty)
    where counterparty is not null;

-- ── Job kind ─────────────────────────────────────────────────────────────
-- Statement extraction is one LLM call per statement over a PDF that may hold
-- several months, so it goes through the queue for the same reason intake does.
alter table jobs drop constraint if exists jobs_kind_check;
alter table jobs
    add constraint jobs_kind_check
    check (kind in ('matter_intake', 'statement_ingest'));

-- Statement ingest already knows its matter; matter intake does not have one
-- yet, which is why this is nullable. It also lets the exceptions queue and
-- the job list be filtered per matter.
alter table jobs
    add column if not exists matter_id integer references matters (id) on delete cascade;

create index if not exists idx_jobs_matter on jobs (matter_id, created_at desc);

notify pgrst, 'reload schema';

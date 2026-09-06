-- 032_category_rules.sql
--
-- Filing transactions by rule, and being able to prove which ones were.
--
-- Two things, and the order matters. The provenance columns come first because
-- without them automatic categorization is not reviewable, not reversible, and
-- not defensible:
--
--   * Which of these three thousand lines did a person actually look at?
--   * Why is this one in Household Supplies?
--   * The Target rule was wrong for this client — undo just those.
--
-- None of those has an answer today. `set_category` writes category_id and
-- nothing else, so even a paralegal's own work leaves no trace. That was
-- tolerable while every category came from a person; it stops being tolerable
-- the moment a machine files at volume.
--
-- ON THE PRINCIPLE THIS CHANGES. statement_service says extraction never sets
-- category_id, "because an FIS total is only as defensible as the person who
-- can testify to it". That principle stands and is not weakened here: what it
-- rules out is the *model's guess*, which is unexplainable and varies run to
-- run. A keyword rule is the opposite — written by the firm, deterministic, and
-- answerable in one sentence on the stand: "that is Household Supplies because
-- the description contains WALMART." What makes it defensible is that the rule,
-- and the fact a rule rather than a person applied it, are both on the record.
-- Hence this migration.
--
-- Run after 031.

-- ── Provenance on the transaction ────────────────────────────────────────

alter table financial_account_transactions
    add column if not exists category_source            text,
    add column if not exists category_rule_id           integer,
    add column if not exists category_set_by_staff_id   integer references staff (id),
    add column if not exists category_set_at            timestamptz,
    add column if not exists category_reviewed_by_staff_id integer references staff (id),
    add column if not exists category_reviewed_at       timestamptz;

-- 'human' | 'rule' | 'similarity'. NULL means the line has never been
-- categorized at all, which is not the same as a person having decided it
-- belongs nowhere — that case is a human source with a null category_id.
alter table financial_account_transactions
    drop constraint if exists financial_account_transactions_category_source_check;

alter table financial_account_transactions
    add constraint financial_account_transactions_category_source_check check (
        category_source is null
        or category_source in ('human', 'rule', 'similarity')
    );

-- The work queue: filed by machine, nobody has confirmed it. This is the index
-- behind "show me what the rules did so I can check it".
create index if not exists idx_transactions_unreviewed_auto
    on financial_account_transactions (financial_account_id)
    where category_source in ('rule', 'similarity') and category_reviewed_at is null;

comment on column financial_account_transactions.category_source is
    'Who filed this line: human, rule, or similarity. NULL means never categorized.';

comment on column financial_account_transactions.category_reviewed_at is
    'When a person confirmed an automatic assignment. Reviewed-and-correct has to be '
    'distinguishable from never-looked-at, or the queue never empties.';


-- ── The rules ────────────────────────────────────────────────────────────

create table if not exists transaction_category_rules (
    id            serial      primary key,

    -- NULL is a firm-wide rule, the same two-layer shape as transaction_tags
    -- and fis_category_settings. A matter row covers the client who owns a gas
    -- station and whose EXXON lines are revenue rather than fuel.
    matter_id     integer     references matters (id) on delete cascade,

    -- Matched case- and punctuation-insensitively, so WALMART finds
    -- "WAL-MART #1234", "WAL MART SUPERCENTER" and "WALMART.COM". Normalisation
    -- happens in the service, on both sides, rather than being stored twice.
    pattern       text        not null,

    category_id   integer     not null
                      references transaction_categories (id) on delete cascade,

    -- Lower fires first. WALMART PHARMACY has to beat WALMART, or medical
    -- spending lands in household supplies.
    priority      integer     not null default 100,

    -- 'any' | 'credit' | 'debit'. PAYROLL arriving is income; PAYROLL leaving is
    -- a business expense. One column, and it prevents a confident mistake.
    applies_to    text        not null default 'any',

    is_active     boolean     not null default true,

    -- Why the rule exists, for whoever inherits it.
    note          text,

    created_at    timestamptz not null default now(),
    updated_at    timestamptz,

    constraint transaction_category_rules_applies_to check (
        applies_to in ('any', 'credit', 'debit')
    ),
    constraint transaction_category_rules_pattern check (
        length(btrim(pattern)) >= 3
    )
);

-- The same pattern may appear twice with different sign constraints — that is
-- the PAYROLL case — so the key includes applies_to.
create unique index if not exists idx_category_rules_firm_pattern
    on transaction_category_rules (lower(btrim(pattern)), applies_to)
    where matter_id is null;

create unique index if not exists idx_category_rules_matter_pattern
    on transaction_category_rules (matter_id, lower(btrim(pattern)), applies_to)
    where matter_id is not null;

create index if not exists idx_category_rules_lookup
    on transaction_category_rules (matter_id, priority) where is_active;

-- Deliberately no FK from the transaction to the rule. A rule is editable and
-- deletable; the record that a rule filed this line has to survive that, or the
-- audit trail evaporates exactly when somebody deletes the rule that caused the
-- problem they are investigating.
comment on column financial_account_transactions.category_rule_id is
    'The rule that filed this line. Intentionally not a foreign key: the trail must '
    'outlive the rule, including when the rule is deleted because it was wrong.';

create or replace trigger trg_transaction_category_rules_updated_at
    before update on transaction_category_rules
    for each row execute function set_updated_at();


-- ── Seed: bank vocabulary only ───────────────────────────────────────────
--
-- Deliberately conservative. These are mechanical terms that mean the same
-- thing at every institution, so a wrong assignment is close to impossible.
--
-- MERCHANT RULES ARE NOT SEEDED, on purpose. Whether WALMART is Groceries or
-- Supplies is a judgment about a household, not a fact about a bank, and it
-- differs by client. So is whether "TRANSFER" means money between the parties'
-- own accounts or a payment to a third party — "INST XFER PAYPAL WEB" is not an
-- interaccount transfer, and a blanket rule on the word would file it as one.
-- Those belong to the firm and are added as they come up.
--
-- Categories are looked up by description rather than by id, so a chart that
-- numbers things differently simply gets fewer seeded rules instead of wrong
-- ones.

insert into transaction_category_rules (pattern, category_id, priority, applies_to, note)
select v.pattern, c.id, v.priority, v.applies_to, v.note
from (values
    ('ATM WITHDRAWAL',      'ATM and Other Cash Withdrawals', 20, 'debit',
     'Bank vocabulary, identical across institutions.'),
    ('CASH WITHDRAWAL',     'ATM and Other Cash Withdrawals', 20, 'debit',
     'Bank vocabulary, identical across institutions.'),
    ('OVERDRAFT FEE',       'Bank Fees',                      20, 'debit',
     'Bank vocabulary, identical across institutions.'),
    ('MONTHLY SERVICE FEE', 'Bank Fees',                      20, 'debit',
     'Bank vocabulary, identical across institutions.'),
    ('INTEREST PAYMENT',    'Interest Income',                20, 'credit',
     'Credit only: interest CHARGED is a different line entirely.'),
    ('INTEREST PAID',       'Interest Income',                20, 'credit',
     'Credit only: interest CHARGED is a different line entirely.')
) as v(pattern, category, priority, applies_to, note)
join transaction_categories c on c.description = v.category
on conflict do nothing;

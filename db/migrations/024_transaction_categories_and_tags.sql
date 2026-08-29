-- ═══════════════════════════════════════════════════════════════════════════
-- 024_transaction_categories_and_tags.sql
--
-- Two independent classification axes over transactions, because they answer
-- two different questions and must not be conflated:
--
--   CATEGORY  one per transaction, a firm-wide hierarchy. Drives the Financial
--             Information Statement — the personal income statement filed for a
--             temporary orders hearing. Every line lands in exactly one bucket
--             or the FIS double-counts.
--
--   TAGS      many per transaction, two layers (firm-wide + per matter). Drives
--             everything else: the TRE 1006 summaries for waste, constructive
--             fraud, and reimbursement claims. A line can belong to several
--             exhibits at once, which is precisely why this cannot be a column.
--
-- Also adds the two fields an exhibit needs to cite its source: the physical
-- page the line was printed on, and the Bates number stamped on that page.
--
-- Run after 023.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Categories ───────────────────────────────────────────────────────────
-- Firm-wide and hierarchical to arbitrary depth: Housing > Utilities > Gas.
-- Not per-matter on purpose — an FIS is only comparable across cases if every
-- case buckets to the same chart.
create table if not exists transaction_categories (
    id             serial      primary key,
    description    text        not null,
    parent_id      integer     references transaction_categories (id) on delete restrict,
    -- Sort key within the whole tree, not within the parent. The seed leaves
    -- gaps of five so a category can be slotted in later without a renumber.
    display_order  integer     not null default 0,
    -- Whether this bucket appears on the Financial Information Statement.
    -- Investment mechanics (a stock split, a transfer between the parties' own
    -- accounts) move money without being income or an expense; counting them
    -- overstates both sides of the statement.
    include_in_fis boolean     not null default true,
    -- Retire a category without breaking the transactions already filed under
    -- it. Deleting one is blocked while it is in use (see the FK below).
    is_active      boolean     not null default true,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),

    constraint transaction_categories_not_own_parent check (parent_id is distinct from id)
);

-- Sibling names must be distinct, case-insensitively. coalesce because a unique
-- index treats each NULL parent as distinct, which would let two top-level
-- "Housing" rows coexist.
create unique index if not exists idx_transaction_categories_sibling_name
    on transaction_categories (coalesce(parent_id, 0), lower(description));

create index if not exists idx_transaction_categories_parent
    on transaction_categories (parent_id, display_order);

-- ── Tags ─────────────────────────────────────────────────────────────────
-- matter_id NULL is a firm-wide tag available on every matter; a value scopes
-- the tag to one case. Both live in one table so a transaction's tag list is a
-- single join rather than a union.
create table if not exists transaction_tags (
    id            serial      primary key,
    matter_id     integer     references matters (id) on delete cascade,
    label         text        not null,
    description   text,
    -- Tailwind-ish token chosen by the user, e.g. 'amber'. Presentation only.
    color         text,
    display_order integer     not null default 0,
    is_active     boolean     not null default true,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- Firm-wide labels unique among themselves; matter labels unique within their
-- matter. A matter may reuse a firm-wide label, and that is intentional — the
-- narrower one wins in that case's UI.
create unique index if not exists idx_transaction_tags_firm_label
    on transaction_tags (lower(label)) where matter_id is null;

create unique index if not exists idx_transaction_tags_matter_label
    on transaction_tags (matter_id, lower(label)) where matter_id is not null;

create index if not exists idx_transaction_tags_matter
    on transaction_tags (matter_id, display_order);

-- ── Transaction ⇄ tag ────────────────────────────────────────────────────
-- Surrogate id so BaseRepository's update/delete by id still work; the real
-- key is the pair.
create table if not exists financial_account_transaction_tags (
    id                 bigserial   primary key,
    transaction_id     bigint      not null
                           references financial_account_transactions (id) on delete cascade,
    tag_id             integer     not null
                           references transaction_tags (id) on delete cascade,
    -- Who applied it. Tagging is an attorney judgment and gets cross-examined.
    tagged_by_staff_id integer     not null references staff (id),
    created_at         timestamptz not null default now(),

    constraint financial_account_transaction_tags_uniq unique (transaction_id, tag_id)
);

-- Both directions get used: "tags on this line" when rendering a row, and
-- "lines carrying this tag" when building an exhibit.
create index if not exists idx_transaction_tags_by_tag
    on financial_account_transaction_tags (tag_id, transaction_id);

-- ── Account succession ───────────────────────────────────────────────────
-- An account that is reissued — a card replaced after fraud, a bank migration,
-- a rollover — arrives as several accounts with different numbers. Read
-- separately they look like three half-produced accounts with alarming gaps in
-- the statement record; read as a chain they are one continuous history and
-- the gaps close. This is what the production-completeness view walks.
--
-- Points backwards (at the predecessor) so a new number can be linked the day
-- it shows up, before anyone knows whether it will itself be replaced.
alter table financial_accounts
    add column if not exists antecedent_account_id integer
        references financial_accounts (id) on delete set null;

-- A chain is single-file in both directions: two accounts cannot both claim the
-- same predecessor, or "the next statement" stops being a question with one
-- answer. Partial so the many standalone accounts do not collide on NULL.
create unique index if not exists idx_financial_accounts_antecedent
    on financial_accounts (antecedent_account_id)
    where antecedent_account_id is not null;

alter table financial_accounts
    drop constraint if exists financial_accounts_not_own_antecedent;
alter table financial_accounts
    add constraint financial_accounts_not_own_antecedent
        check (antecedent_account_id is distinct from id);

-- ── Transaction columns ──────────────────────────────────────────────────
alter table financial_account_transactions
    -- ON DELETE RESTRICT, not SET NULL: quietly un-categorizing evidence
    -- because someone tidied the chart is how an FIS silently loses a line.
    add column if not exists category_id integer references transaction_categories (id) on delete restrict,
    -- Page of the source PDF this line was printed on, 1-based.
    add column if not exists physical_page_number integer,
    -- Bates number stamped on that page, as printed. Text, not a number — real
    -- stamps carry a prefix and fixed-width zero padding ("KF-000142") and both
    -- halves matter when the exhibit cites it.
    add column if not exists bates_number text;

create index if not exists idx_transactions_category
    on financial_account_transactions (category_id)
    where category_id is not null;

create index if not exists idx_transactions_bates
    on financial_account_transactions (bates_number)
    where bates_number is not null;

-- Substring search over descriptions. pg_trgm is installed by 001; without this
-- index an ILIKE '%...%' over a production of statements is a sequential scan.
create index if not exists idx_transactions_description_trgm
    on financial_account_transactions using gin (description gin_trgm_ops);

-- ── Seed: categories ─────────────────────────────────────────────────────
-- Explicit ids so the parent references below are stable, and so a re-run is a
-- no-op rather than a second copy of the tree.
insert into transaction_categories (id, description, parent_id, display_order, include_in_fis) values
    ( 1, 'Housing',                          null,   0, true),
    ( 2, 'Rent',                                1, 100, true),
    ( 3, 'Mortgage Payment',                    1, 105, true),
    ( 4, 'Property Taxes',                      1, 110, true),
    ( 5, 'Utilities',                           1, 115, true),
    ( 6, 'Electricity',                         5, 120, true),
    ( 7, 'Gas',                                 5, 125, true),
    ( 8, 'Internet',                            5, 130, true),
    ( 9, 'Transportation',                   null, 200, true),
    (10, 'Automobile Payment',                  9, 205, true),
    (11, 'Auto Insurance',                      9, 210, true),
    (12, 'Repairs/Maintenance',                 9, 215, true),
    (13, 'Ride share (Uber, Lyft, Waymo)',      9, 220, true),
    (14, 'Tolls',                               9, 225, true),
    (15, 'Parking',                             9, 230, true),
    (16, 'Non-Income Investment Activity',   null, 9000, false),
    (17, 'Stock Split',                        16, 9005, false)
on conflict (id) do nothing;

-- The seed set ids by hand, so the sequence is still at 1 and the next insert
-- from the app would collide.
select setval(
    pg_get_serial_sequence('transaction_categories', 'id'),
    (select max(id) from transaction_categories)
);

-- ── Seed: firm-wide tags ─────────────────────────────────────────────────
-- The general layer. Matter-specific tags ("Waste: Sister's Wedding") are
-- created from the matter's Financials page as they come up.
insert into transaction_tags (matter_id, label, description, color, display_order) values
    (null, 'Waste Claim',        'Supports a claim of waste of community assets',              'red',    100),
    (null, 'Reimbursement Claim','Supports a reimbursement claim between estates',             'amber',  110),
    (null, 'Needs Review',       'Flagged by a human for a second look',                       'blue',   120),
    (null, 'Manually Corrected', 'A value on this line was corrected against the source page', 'purple', 130)
on conflict do nothing;

notify pgrst, 'reload schema';

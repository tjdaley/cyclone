-- 031_fis_category_settings.sql
--
-- How often a category is paid or received — which is arithmetic, not decoration.
--
-- The Financial Information Statement reports an average MONTHLY figure over a
-- window of whole months. For anything paid monthly that is simply the window
-- total divided by the number of months. For anything paid less often it is
-- wrong, and wrong in a way that moves:
--
--     Property taxes, $3,600 once in January.
--     Window Jan-Feb  -> 3600 / 2  = $1,800/mo
--     Window Jan-Mar  -> 3600 / 3  = $1,200/mo
--     Window Jan-Dec  -> 3600 / 12 =   $300/mo
--
-- Same facts, three sworn figures, and only the last is true. The payment buys
-- twelve months of coverage; dividing it across the two months that happen to
-- be in the sample charges all of it to those months. Worse, the number changes
-- every time the report is re-run, which is a cross-examination waiting to
-- happen: "Counsel, which figure did your client swear to?"
--
-- With a recurrence on record the figure is computed from the trailing twelve
-- months and divided by twelve, so it is stable no matter when the report runs,
-- it survives an expense paid in two instalments (two tax parcels sum
-- correctly, where averaging per occurrence would halve them), and it still
-- finds a payment that falls OUTSIDE the report window — a Jan-Feb window on a
-- tax paid last November otherwise shows a blank line, which is as wrong as an
-- inflated one.
--
-- The same row also supplies the legend the exhibit prints — "Property Taxes
-- (paid annually) $300.00" — so the witness explains the figure before opposing
-- counsel gets to ask about it.
--
-- SCOPED TO THE PERSON, NOT THE MATTER. A payment schedule is a fact about
-- someone's finances, not about a lawsuit: the same client may have matters in
-- several counties from successive marriages, and they pay property taxes on
-- the same schedule in all of them. Two layers, the same shape as
-- transaction_tags: a row with neither party set is the firm-wide default
-- ("property taxes are usually annual"), and a row naming a party overrides it.
--
-- Note that opposing_parties is itself matter-scoped (matter_id NOT NULL), so
-- settings for the other side stay with that matter while a client's follow
-- the client. That is a property of the existing schema, not a choice made
-- here.
--
-- AN ABSENT ROW MEANS TODAY'S BEHAVIOUR — window total over window months. The
-- table starts empty and nothing changes until somebody says otherwise, so this
-- migration is safe to apply before any of the code that reads it.
--
-- Run after 030.

create table if not exists fis_category_settings (
    id                  serial      primary key,

    -- Exactly one of these, or neither. Neither = the firm-wide default.
    client_id           integer     references clients (id) on delete cascade,
    opposing_party_id   integer     references opposing_parties (id) on delete cascade,

    category_id         integer     not null
                            references transaction_categories (id) on delete cascade,

    -- How often money moves in this category for this person.
    --   weekly | biweekly | semimonthly | monthly  -> window total / window months
    --   quarterly | semiannual | annual            -> trailing 12 months / 12
    --   irregular                                  -> as monthly; the legend differs
    -- 'irregular' is for genuinely unscheduled spending (medical, repairs)
    -- where "as incurred" is the honest legend.
    recurrence          text        not null default 'monthly',

    -- The attorney's own figure, and it wins over anything derived. Needed when
    -- the production does not reach back a full year: we can compute what the
    -- statements show, but only a person can say "the bill is $3,600".
    -- Named for its provenance — on a table whose output is sworn testimony,
    -- where a number came from is part of the number.
    --
    -- SIGNED, like every other amount in this schema: negative for money going
    -- out, positive for money coming in. It cannot borrow its sign from the
    -- transactions, because the case for stating a figure at all is that the
    -- transactions do not show one.
    stated_annual_amount numeric(14,2),

    -- Extra legend printed beside the line, when the recurrence alone does not
    -- explain it ("escrowed with the mortgage", "paid by employer").
    note                text,

    created_at          timestamptz not null default now(),
    updated_at          timestamptz,

    -- A row is the firm's default or one party's, never both.
    constraint fis_category_settings_one_party check (
        client_id is null or opposing_party_id is null
    ),

    constraint fis_category_settings_recurrence check (
        recurrence in (
            'weekly', 'biweekly', 'semimonthly', 'monthly',
            'quarterly', 'semiannual', 'annual', 'irregular'
        )
    )
);

-- Written before the sign question was settled: an early copy of this file
-- constrained stated_annual_amount to be non-negative, which makes it
-- impossible to state an expense. Dropped here so re-running the file repairs
-- an installation that got the first version.
alter table fis_category_settings
    drop constraint if exists fis_category_settings_annual_amount;

-- One row per category at each scope. Partial indexes rather than a single
-- composite one, because NULL is not distinct from NULL in a plain unique
-- index and the firm-wide layer would allow duplicates.
create unique index if not exists idx_fis_settings_firm_category
    on fis_category_settings (category_id)
    where client_id is null and opposing_party_id is null;

create unique index if not exists idx_fis_settings_client_category
    on fis_category_settings (client_id, category_id)
    where client_id is not null;

create unique index if not exists idx_fis_settings_opposing_category
    on fis_category_settings (opposing_party_id, category_id)
    where opposing_party_id is not null;

-- The read path resolves a whole chart at once: every setting for one party
-- plus every firm default, then the narrower wins per category.
create index if not exists idx_fis_settings_client
    on fis_category_settings (client_id) where client_id is not null;

create index if not exists idx_fis_settings_opposing
    on fis_category_settings (opposing_party_id) where opposing_party_id is not null;

-- 003 defines set_updated_at() and applies it to the tables that existed then;
-- the financial tables in 023/024 carry the column without the trigger, so it
-- has never been populated for them. Wiring it here is deliberate: this table
-- records an attorney judgment that changes a figure on a sworn document, and
-- when that judgment last changed is worth knowing.
create or replace trigger trg_fis_category_settings_updated_at
    before update on fis_category_settings
    for each row execute function set_updated_at();

comment on table fis_category_settings is
    'Payment/receipt frequency per category, per person. Drives the monthly '
    'averaging on the Financial Information Statement and the legend printed '
    'beside each line. No row means window total over window months.';

comment on column fis_category_settings.stated_annual_amount is
    'Attorney-supplied annual figure. Overrides anything derived from the '
    'transactions — used when the production does not span a full year.';

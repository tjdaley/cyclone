-- 033_creditor_discovery.sql
--
-- Finding the credit accounts a production does not contain.
--
-- The transfer scan (account_discovery_service) finds an account because the
-- description prints its number: "Transfer from XXX4070". A payment to a
-- creditor almost never does. Measured against one production's payment lines:
--
--     12/21 Payment To Chase Card Ending IN 9547              <- names a number
--     ACH PMT AMEX EPAYMENT 0005000008 ID #-M3630 TRACE #-... <- names nobody
--     10/16 Online Payment 22398267106 To City of Lewisville  <- names nobody
--
-- Two of roughly a dozen credit relationships carried a number. The rest are
-- identifiable only by WHO was paid -- and "Online Payment To Mr. Cooper" (a
-- mortgage servicer) and "Online Payment To Frontier" (an ISP) are the same
-- sentence. No pattern separates them, because the difference is not in the
-- text. It is in knowing what the counterparty is.
--
-- So this migration adds the two places that knowledge can live:
--
--   1. is_liability on the category. When a paralegal files a payment under
--      "Credit Card Payments" or "Mortgage Payment", that filing IS the
--      assertion that the counterparty is a creditor. Categorization work
--      already being done becomes account discovery for free, and the answer
--      is explainable in one sentence: "it is a creditor because we filed it
--      as a credit card payment."
--
--   2. transaction_payee_classifications, for the residue -- the payees nobody
--      has categorized yet. Both verdicts are recorded, because both are
--      worth keeping: "this is a creditor" adds a row to the report, and
--      "this is not" removes it permanently. Without the second one, a
--      paralegal re-triages Atmos Energy and the City of Lewisville on every
--      matter, forever, and stops reading the list.
--
-- Two layers, as with tags, category rules, and FIS settings: matter_id NULL
-- is the firm's answer, a matter row overrides it. Utilities and grocery
-- chains are firm-wide knowledge; a Zelle payee who might be a private lender
-- is not.
--
-- Run after 032.

-- -- Which categories name a debt ------------------------------------------

alter table transaction_categories
    add column if not exists is_liability boolean not null default false;

comment on column transaction_categories.is_liability is
    'True when money filed here was paid to a creditor: a card issuer, a lender, a '
    'mortgage servicer. Read by the creditor-discovery scan, because a payment filed '
    'under a liability category names an account somebody may not have produced. '
    'Distinct from include_in_fis, which asks whether the line belongs on the sworn '
    'statement at all.';

-- Seeded by description rather than by id, the same rule 032 uses: a chart
-- that names things differently gets fewer flags instead of wrong ones. The
-- firm sets the rest -- see the inspection query at the foot of this file.
update transaction_categories set is_liability = true
where description in (
    'Mortgage Payment', 'Mortgage', 'Second Mortgage', 'Home Equity Loan',
    'Home Equity Line of Credit', 'Line of Credit',
    'Credit Card Payment', 'Credit Card Payments', 'Credit Cards',
    'Credit Card Payments (not on FIS)',
    'Auto Loan', 'Auto Loan Payment', 'Car Payment', 'Vehicle Loan',
    'Student Loan', 'Student Loans',
    'Personal Loan', 'Loan Payment', 'Installment Loan',
    'Note Payable', 'Notes Payable', 'Boat Loan', 'RV Loan', 'Medical Debt', 'Legal Fees Owed',
    'Child Support Arrearage', 'Spousal Support Arrearage','Judgment','Real Estate Assessment',
    'Federal Tax from Prior Years','Other Tax from Prior Years'
);


-- -- What the firm has decided about a payee -------------------------------

create table if not exists transaction_payee_classifications (
    id             serial      primary key,

    -- NULL is the firm's answer, offered on every matter. Atmos Energy is not
    -- a creditor in any case anybody will ever open; a Zelle payee who might
    -- be a private lender is a judgment about one household.
    matter_id      integer     references matters (id) on delete cascade,

    -- The normalized payee, matched the way transaction_category_rules
    -- matches: case- and punctuation-blind, on word boundaries. Normalisation
    -- lives in the service, on both sides, rather than being stored twice.
    pattern        text        not null,

    -- 'creditor'     -- report payments to this payee as an undisclosed account
    -- 'not_creditor' -- a vendor. Stop showing it, on this matter or firm-wide.
    classification text        not null,

    -- What to call it in the report and in the request for production. The
    -- normalized payee is a scraped fragment ("CITI CARD ONLINE CITICTP");
    -- this is what a human would write on a motion.
    creditor_name  text,

    -- credit_card | loan | mortgage | line_of_credit | other. It changes what
    -- you ask for: a card means monthly statements, a mortgage means a payoff
    -- statement and a note.
    creditor_type  text,

    note           text,
    is_active      boolean     not null default true,

    -- Who decided. A classification suppresses evidence from a report that
    -- backs a motion, so it is attributable -- the same reason tagging records
    -- who tagged.
    decided_by_staff_id integer references staff (id),

    created_at     timestamptz not null default now(),
    updated_at     timestamptz,

    constraint transaction_payee_classifications_classification check (
        classification in ('creditor', 'not_creditor')
    ),
    constraint transaction_payee_classifications_creditor_type check (
        creditor_type is null
        or creditor_type in ('credit_card', 'loan', 'mortgage', 'line_of_credit', 'other')
    ),
    constraint transaction_payee_classifications_pattern check (
        length(btrim(pattern)) >= 3
    )
);

create unique index if not exists idx_payee_classifications_firm_pattern
    on transaction_payee_classifications (lower(btrim(pattern)))
    where matter_id is null;

create unique index if not exists idx_payee_classifications_matter_pattern
    on transaction_payee_classifications (matter_id, lower(btrim(pattern)))
    where matter_id is not null;

create index if not exists idx_payee_classifications_lookup
    on transaction_payee_classifications (matter_id) where is_active;

create or replace trigger trg_transaction_payee_classifications_updated_at
    before update on transaction_payee_classifications
    for each row execute function set_updated_at();


-- -- Seed: national card issuers and mortgage servicers --------------------
--
-- Unlike merchant categorization, this list is NOT a judgment about a
-- household. American Express issues credit; that is true of every client the
-- firm will ever have, and nobody will dispute it. Seeding it costs nothing
-- and saves the same triage on every matter.
--
-- Patterns are the strings the scan will actually see on a statement, not the
-- brand's legal name.
--
-- Deliberately NOT seeded: anything local, anything that could be a vendor
-- under another name, and every utility. A wrong 'not_creditor' hides an
-- account permanently and silently, which is the failure this whole module
-- exists to avoid -- so nothing is suppressed by seed. The firm suppresses,
-- one payee at a time, on the record.

insert into transaction_payee_classifications
    (matter_id, pattern, classification, creditor_name, creditor_type, note)
values
    (null, 'AMEX',             'creditor', 'American Express',        'credit_card', 'National issuer.'),
    (null, 'AMERICAN EXPRESS', 'creditor', 'American Express',        'credit_card', 'National issuer.'),
    (null, 'DISCOVER',         'creditor', 'Discover',                'credit_card', 'National issuer.'),
    (null, 'APPLECARD',        'creditor', 'Apple Card (Goldman Sachs Bank)', 'credit_card', 'National issuer.'),
    (null, 'CHASE CARD',       'creditor', 'Chase',                   'credit_card', 'National issuer.'),
    (null, 'CITI CARD',        'creditor', 'Citi',                    'credit_card', 'National issuer.'),
    (null, 'CITI AUTOPAY',     'creditor', 'Citi',                    'credit_card', 'National issuer.'),
    (null, 'CAPITAL ONE',      'creditor', 'Capital One',             'credit_card', 'National issuer.'),
    (null, 'BARCLAYCARD',      'creditor', 'Barclays',                'credit_card', 'National issuer.'),
    (null, 'SYNCHRONY',        'creditor', 'Synchrony Bank',          'credit_card', 'Issues many store cards.'),
    (null, 'COMENITY',         'creditor', 'Comenity Bank',           'credit_card', 'Issues many store cards.'),
    (null, 'BREAD FINANCIAL',  'creditor', 'Bread Financial',         'credit_card', 'Issues store cards.'),
    (null, 'MR COOPER',        'creditor', 'Mr. Cooper (Nationstar)', 'mortgage',    'Mortgage servicer.'),
    (null, 'ROCKET MORTGAGE',  'creditor', 'Rocket Mortgage',         'mortgage',    'Mortgage servicer.'),
    (null, 'SHELLPOINT',       'creditor', 'Shellpoint',              'mortgage',    'Mortgage servicer.'),
    (null, 'LOANCARE',         'creditor', 'LoanCare',                'mortgage',    'Mortgage servicer.'),
    (null, 'FREEDOM MORTGAGE', 'creditor', 'Freedom Mortgage',        'mortgage',    'Mortgage servicer.'),
    (null, 'SALLIE MAE',       'creditor', 'Sallie Mae',              'loan',        'Student lender.'),
    (null, 'NELNET',           'creditor', 'Nelnet',                  'loan',        'Student loan servicer.'),
    (null, 'ALLY FINANCIAL',   'creditor', 'Ally Financial',          'loan',        'Auto lender.'),
    (null, 'TOYOTA FINANCIAL', 'creditor', 'Toyota Financial',        'loan',        'Auto lender.'),
    (null, 'FORD CREDIT',      'creditor', 'Ford Motor Credit',       'loan',        'Auto lender.')
on conflict do nothing;


-- -- After running this, check what got flagged ----------------------------
--
--   select id, description, is_liability, include_in_fis
--     from transaction_categories
--    where is_liability
--    order by display_order, id;
--
-- and set anything the seed missed, by id:
--
--   update transaction_categories set is_liability = true where id in (...);
--
-- A category flagged here does not change the FIS. include_in_fis still
-- decides that, and a credit card payment must stay off the statement or it
-- double-counts the withdrawal that funded it.

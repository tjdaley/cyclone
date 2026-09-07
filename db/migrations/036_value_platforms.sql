-- 036_value_platforms.sql
--
-- The accounts a production does not contain because they are not at a bank.
--
-- 033 found credit accounts by asking who was PAID. This finds the other half
-- of the same hole: money sitting in, or moving through, a custodian that never
-- issues anything a paralegal would recognise as a bank statement. Venmo,
-- PayPal, Cash App, Coinbase, Robinhood, a dozen brokerages. Every one of them
-- holds a balance, none of them appears in a production nobody asked for, and
-- the client does not think of them as accounts.
--
-- WHY THIS IS NOT THE 033 MECHANISM, even though the description looks the same
-- ---------------------------------------------------------------------------
-- 033 needs a human ruling because the answer genuinely is not in the text:
-- "Online Payment To Mr. Cooper" (a mortgage servicer) and "Online Payment To
-- Frontier" (an ISP) are the same sentence, and no pattern separates them.
--
-- "VENMO PAYMENT" is not like that. Venmo holds a balance on every matter that
-- will ever be opened, and so does Coinbase, and so does Robinhood. The answer
-- is knowable in advance and identical everywhere. Building a triage queue for
-- it would make a paralegal answer, several times a week, a question the firm
-- can answer once -- and a queue nobody can finish is a queue nobody reads.
--
-- So these are SEEDED, and the ruling table catches only the long tail: a local
-- credit union's payment app, some fintech that did not exist last year.
--
-- SEED ADDITIONS, NEVER SEED SUPPRESSIONS
-- ---------------------------------------------------------------------------
-- 033 forbids seeding a 'not_creditor' row, and rightly: suppression hides
-- evidence, and hiding evidence must always be somebody's recorded decision.
-- Seeding does the opposite here. A seeded 'custodian' row can only ever ADD a
-- line to a report for a person to look at; the worst case is a paralegal
-- reading one row and dismissing it. That asymmetry is the whole rule, and it
-- is why this file may seed and 033 may not.
--
-- ZELLE IS DELIBERATELY ABSENT
-- ---------------------------------------------------------------------------
-- Zelle holds no balance. It is an interbank rail -- money moves account to
-- account with nothing resting in between -- so there is no Zelle statement to
-- compel and no Zelle balance to divide. Listing it as an account would be a
-- false statement of exactly the class this module exists to prevent, the same
-- error as reading the routing number inside an ACH trace as an account.
--
-- Zelle traffic is still evidence; it just answers a different question. A
-- repeated Zelle to one individual is a private loan, a gift, support of a
-- third party, or money parked with a friend -- a finding about a COUNTERPARTY,
-- not an account. That is what tags are for, and the paralegals already do it.
--
-- Run after 035.

-- ── A platform can hold several kinds of account ─────────────────────────────
--
-- PayPal is a balance, PayPal Credit (a Synchrony line), a debit Mastercard and
-- PayPal Savings. Cash App is a balance, bitcoin, a card and Borrow. Robinhood
-- is a brokerage, a margin loan and now an IRA. Which of them a household holds
-- is NOT determinable from a transaction description -- "PAYPAL INST XFER"
-- appears whichever it is -- so the report asks for all of them and lets the
-- responding party say which do not exist. An extra line in a request for
-- production costs nothing; the omitted one is what gets exploited.
--
-- Kept separate from creditor_type rather than widening it. creditor_type is a
-- creditor's single kind and drives what you request (a card means statements,
-- a mortgage means a payoff and a note); this is a set, and only a custodian
-- row has one. Two classifications, two shapes, nothing to migrate.
alter table transaction_payee_classifications
    add column if not exists holds text[] not null default '{}';

comment on column transaction_payee_classifications.holds is
    'For a custodian row: every kind of account this platform can hold, so the request for '
    'production asks for all of them. Empty on a creditor row, which uses creditor_type -- a '
    'creditor has one kind and a platform has several.';

-- ── Widen the two vocabularies ───────────────────────────────────────────────
alter table transaction_payee_classifications
    drop constraint if exists transaction_payee_classifications_classification;

alter table transaction_payee_classifications
    add constraint transaction_payee_classifications_classification check (
        classification in ('creditor', 'not_creditor', 'custodian')
    );

comment on column transaction_payee_classifications.classification is
    'creditor -- payments to this payee name a debt account. '
    'custodian -- this counterparty HOLDS value for our party: a balance, a portfolio, '
    'crypto. Reported from traffic in either direction, because any traffic proves the '
    'account exists. '
    'not_creditor -- a vendor. Stop showing it. Never seeded: suppression is always '
    'somebody''s recorded decision.';

alter table transaction_payee_classifications
    add constraint transaction_payee_classifications_holds check (
        holds <@ array[
            'deposit', 'brokerage', 'crypto', 'retirement',
            'credit_card', 'line_of_credit', 'loan', 'other'
        ]::text[]
    );

-- A custodian row is useless without saying what it holds -- that set IS the
-- request for production. A creditor row must not carry one, or two columns
-- would answer the same question and drift.
--
-- BOTH SIDES ARE coalesce()d, and that is not decoration. array_length() of an
-- empty array is NULL, not 0, and a CHECK constraint passes when its expression
-- is NULL as readily as when it is TRUE -- so the obvious spelling of the first
-- branch would have let through exactly the row it exists to reject: a custodian
-- holding nothing, which produces a finding that asks for no documents.
alter table transaction_payee_classifications
    add constraint transaction_payee_classifications_holds_shape check (
        (classification = 'custodian' and coalesce(array_length(holds, 1), 0) >= 1)
        or (classification <> 'custodian' and coalesce(array_length(holds, 1), 0) = 0)
    );

-- ── Crypto is its own kind of account ────────────────────────────────────────
--
-- It was landing under 'brokerage' or 'other'. It divides differently, it is
-- valued differently (and on a date somebody has to argue for), and it is the
-- thing a party is most likely to move while a case is pending -- so it needs
-- to be filterable. Cheap now, annoying once there is data under the old value.
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
            'retirement', 'hsa', 'loan', 'other'
        )
    );

-- ── Seed: buy-now-pay-later ──────────────────────────────────────────────────
--
-- Real debt, and the debt least likely to be disclosed. It rarely reaches a
-- credit report, the client does not experience it as borrowing, and it shows
-- on a statement as a small recurring debit indistinguishable from a
-- subscription. No code reads these differently from any other creditor ruling
-- -- the 033 scan finds them the moment the rows exist.
--
-- Every one of these companies does exactly one thing, which is lend. There is
-- no Mr. Cooper / Frontier ambiguity to resolve, so a firm-wide answer is
-- the right shape.
insert into transaction_payee_classifications
    (matter_id, pattern, classification, creditor_name, creditor_type, note)
values
    (null, 'AFFIRM',    'creditor', 'Affirm',            'loan', 'Buy-now-pay-later instalment lender'),
    (null, 'KLARNA',    'creditor', 'Klarna',            'loan', 'Buy-now-pay-later instalment lender'),
    (null, 'AFTERPAY',  'creditor', 'Afterpay',          'loan', 'Buy-now-pay-later instalment lender'),
    (null, 'SEZZLE',    'creditor', 'Sezzle',            'loan', 'Buy-now-pay-later instalment lender'),
    (null, 'QUADPAY',   'creditor', 'Zip (formerly Quadpay)', 'loan', 'Buy-now-pay-later instalment lender'),
    (null, 'ZIP CO',    'creditor', 'Zip',               'loan',
        'Buy-now-pay-later. Patterned "ZIP CO" and not "ZIP", which would match a postal code'),
    (null, 'UPLIFT',    'creditor', 'Uplift',            'loan', 'Travel instalment lender'),
    (null, 'KATAPULT',  'creditor', 'Katapult',          'loan', 'Lease-to-own lender'),
    (null, 'PERPAY',    'creditor', 'Perpay',            'loan', 'Instalment lender'),
    (null, 'SPLITIT',   'creditor', 'Splitit',           'loan', 'Instalment lender')
on conflict do nothing;

-- ── Seed: platforms that hold value ──────────────────────────────────────────
--
-- Short and common-word names are held OUT of this list on purpose. "Current",
-- "Dave", "Albert", "Empower", "Public" and "Gemini" are all real platforms and
-- all real English, and the boundary-aware matcher does not save a pattern that
-- is itself an ordinary word -- the same failure as TARGET matching inside
-- STARGETTER LLC, one level up and firm-wide rather than on one matter. They
-- belong in a per-matter ruling, where a person has looked at the description
-- that prompted it.
--
-- Where a platform prints a distinguishing word, the pattern carries it:
-- "ALLY BANK" not "ALLY", "MARCUS BY GOLDMAN" not "MARCUS", "GOOGLE PAY" not
-- "GOOGLE", "MERRILL LYNCH" not "MERRILL", "STASH INVEST" not "STASH",
-- "WISE US" not "WISE", "ACORNS GROW" not "ACORNS". Every one of those was
-- caught by tests/test_platform_seeds.py matching a plausible merchant.
--
-- FOUR ARE KEPT BARE AS A DELIBERATE TRADE, and they are the exception rather
-- than an oversight: SOFI, KRAKEN, VARO and BETTERMENT. Each can be made to
-- collide with an invented business name, but none is an ordinary word, and
-- qualifying them risks the opposite failure -- SoFi alone prints SOFI BANK,
-- SOFI SECURITIES and SOFI LENDING, so any single qualified pattern misses two
-- of its three products. A false positive costs a paralegal five seconds and
-- one glance at the example line; a false negative means an undisclosed
-- account never surfaces at all, which is the failure this whole module is for.
-- The test records these four explicitly, so the trade stays visible.
insert into transaction_payee_classifications
    (matter_id, pattern, classification, creditor_name, holds, note)
values
    -- Peer-to-peer wallets. A balance can sit here indefinitely and nothing
    -- ever calls itself a statement.
    (null, 'VENMO',       'custodian', 'Venmo',       array['deposit'],
        'PayPal-owned wallet. Holds a balance; also a debit card'),
    (null, 'PAYPAL',      'custodian', 'PayPal',      array['deposit', 'credit_card', 'line_of_credit'],
        'Balance, PayPal Credit (Synchrony), debit Mastercard and PayPal Savings'),
    (null, 'CASH APP',    'custodian', 'Cash App',    array['deposit', 'crypto', 'loan'],
        'Block. Balance, bitcoin, card, and Borrow'),
    (null, 'SQUARE CASH', 'custodian', 'Cash App',    array['deposit', 'crypto'],
        'How Cash App printed on older statements'),
    (null, 'APPLE CASH',  'custodian', 'Apple Cash',  array['deposit'],
        'Green Dot-issued balance. Distinct from Apple Card, which is a Goldman debt'),
    (null, 'GOOGLE PAY',  'custodian', 'Google Pay',  array['deposit'],
        'Patterned with PAY, or it matches every Google subscription'),

    -- Crypto. The asset most likely to move while a case is pending.
    (null, 'COINBASE',    'custodian', 'Coinbase',    array['crypto', 'deposit', 'credit_card'],
        'Crypto and a USD balance; also Coinbase Card'),
    (null, 'BINANCE',     'custodian', 'Binance',     array['crypto'], null),
    (null, 'KRAKEN',      'custodian', 'Kraken',      array['crypto'], null),
    (null, 'CRYPTO COM',  'custodian', 'Crypto.com',  array['crypto', 'credit_card'], null),
    (null, 'BLOCKFI',     'custodian', 'BlockFi',     array['crypto'],
        'Defunct, but historical transfers still evidence an account that held value'),
    (null, 'CELSIUS NETWORK', 'custodian', 'Celsius Network', array['crypto'],
        'Defunct. Patterned with NETWORK, or it matches a utility'),

    -- Brokerages and the app-first investment platforms.
    (null, 'ROBINHOOD',   'custodian', 'Robinhood',   array['brokerage', 'crypto', 'retirement', 'loan'],
        'Brokerage, crypto, IRA, and margin -- margin is a debt'),
    (null, 'WEBULL',      'custodian', 'Webull',      array['brokerage', 'crypto'], null),
    -- One row covers both spellings: matching flattens punctuation and spaces
    -- away, so 'E TRADE ACH TRANSFER' and 'ETRADE ACH' both land on this.
    (null, 'ETRADE',      'custodian', 'E*TRADE',     array['brokerage', 'retirement'], null),
    (null, 'FIDELITY',    'custodian', 'Fidelity',    array['brokerage', 'retirement'], null),
    (null, 'VANGUARD',    'custodian', 'Vanguard',    array['brokerage', 'retirement'], null),
    (null, 'SCHWAB',      'custodian', 'Charles Schwab', array['brokerage', 'retirement', 'deposit'], null),
    (null, 'TD AMERITRADE', 'custodian', 'TD Ameritrade', array['brokerage', 'retirement'], null),
    (null, 'INTERACTIVE BROKERS', 'custodian', 'Interactive Brokers', array['brokerage'], null),
    (null, 'MERRILL LYNCH', 'custodian', 'Merrill',   array['brokerage', 'retirement'],
        'Qualified: bare MERRILL is a surname and matched a retirement community'),
    (null, 'BETTERMENT',  'custodian', 'Betterment',  array['brokerage', 'retirement'], null),
    (null, 'WEALTHFRONT', 'custodian', 'Wealthfront', array['brokerage', 'deposit'], null),
    (null, 'ACORNS GROW', 'custodian', 'Acorns',      array['brokerage', 'retirement'],
        'Qualified with the corporate name: bare ACORNS matched a preschool'),
    (null, 'STASH INVEST', 'custodian', 'Stash',      array['brokerage'],
        'Qualified: bare STASH is an ordinary word and matched a barbecue restaurant'),
    (null, 'M1 FINANCE',  'custodian', 'M1 Finance',  array['brokerage', 'retirement'], null),
    (null, 'TASTYWORKS',  'custodian', 'tastytrade',  array['brokerage'], null),
    (null, 'TASTYTRADE',  'custodian', 'tastytrade',  array['brokerage'], null),

    -- Neobanks. They hold deposits and issue no statement anybody thinks to ask
    -- for, because the client calls it "my app".
    (null, 'SOFI',        'custodian', 'SoFi',        array['deposit', 'brokerage', 'crypto', 'loan'], null),
    (null, 'CHIME',       'custodian', 'Chime',       array['deposit'], null),
    (null, 'VARO',        'custodian', 'Varo',        array['deposit'], null),
    (null, 'REVOLUT',     'custodian', 'Revolut',     array['deposit', 'crypto'], null),
    (null, 'WISE US',     'custodian', 'Wise',        array['deposit'],
        'Multi-currency balances. Qualified: bare WISE matched a pizza restaurant'),
    (null, 'TRANSFERWISE', 'custodian', 'Wise',       array['deposit'], null),
    (null, 'ALLY BANK',   'custodian', 'Ally Bank',   array['deposit', 'brokerage'],
        'Patterned with BANK, because "ally" is an ordinary word'),
    (null, 'MARCUS BY GOLDMAN', 'custodian', 'Marcus by Goldman Sachs', array['deposit'],
        'Patterned in full, because "Marcus" is a name')
on conflict do nothing;

-- ── After running: what the firm should look at ──────────────────────────────
--
-- Nothing below runs. These are the two inspection queries worth pasting once
-- the seeds are in, before anybody trusts a report built on them.
--
-- 1. Every seeded ruling, so somebody can disagree with one:
--
--      select pattern, classification, creditor_name, creditor_type, holds, note
--      from transaction_payee_classifications
--      where matter_id is null
--      order by classification, pattern;
--
-- 2. Whether any of these patterns collides with an existing category rule,
--    which would mean the same payee is being described in two places:
--
--      select p.pattern, r.pattern as rule_pattern
--      from transaction_payee_classifications p
--      join transaction_category_rules r
--        on upper(regexp_replace(r.pattern, '[^a-zA-Z0-9]', '', 'g'))
--         = upper(regexp_replace(p.pattern, '[^a-zA-Z0-9]', '', 'g'))
--      where p.matter_id is null;

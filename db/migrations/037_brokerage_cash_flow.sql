-- 037_brokerage_cash_flow.sql
--
-- The chart entries a brokerage statement needs, and the one line that is not a
-- transaction but has to be treated as one.
--
-- ⚠ THIS FILE REPLACES AN EARLIER DRAFT THAT MUST NOT BE RUN, AND REPAIRS IT
-- ---------------------------------------------------------------------------
-- The first draft inserted categories at hard-coded ids 18-27, copying the
-- style of 024. By the time it shipped the firm had populated the chart and
-- those ids were taken -- 18 is Telephone, 19 is HOA Dues, 20 is Property
-- Insurance, 23 is Supplies, 25 is Housekeeping. The category inserts would
-- have been skipped harmlessly by ON CONFLICT, but the keyword rules named
-- those ids as literals, so every dividend would have been filed under
-- Telephone and every margin interest charge under Housekeeping. Silently, and
-- firm-wide.
--
-- Nothing in this file names an id. Categories are found or created BY
-- DESCRIPTION and rules resolve their target the same way, which is the rule
-- 033 already follows: "seeded by description rather than by id -- a chart that
-- names things differently gets fewer flags instead of wrong ones."
--
-- The block at the top deletes any rule the earlier draft created, identified
-- by the exact notes it wrote. Running this file on a database that never saw
-- the draft is a no-op; running it on one that did repairs the damage. Re-run
-- it as often as you like.
--
-- WHAT A BROKERAGE STATEMENT IS FOR, HERE
-- ---------------------------------------------------------------------------
-- This project traces sources and uses of cash. A stock purchase is neither: it
-- exchanges $8,500 of cash for $8,500 of stock inside one account, and the
-- account is worth exactly what it was a second earlier. Nothing entered the
-- marital estate and nothing left it. The same is true of a sale, a redemption,
-- a maturity, an exchange, and a split. Those are not extracted at all -- see
-- statement_service._TRANSACTIONS_SYSTEM. What IS extracted is money crossing
-- the account boundary, plus dividends, interest, fees, and the change in
-- market value the statement prints.
--
-- WHERE INVESTMENT GAIN AND LOSS GOES, AND WHY IT COUNTS
-- ---------------------------------------------------------------------------
-- It is not net resources under Tex. Fam. Code 154.062(b). It is nonetheless
-- economic activity, and Tom's reason is the operative one: a party moving
-- $5,000 a month into a brokerage account while accumulating $4,000 a month of
-- unrealized losses is depleting the marital estate, and a report that cannot
-- see that is a report with a money sink off its radar.
--
-- So the DEFAULT is Investment Gain/Loss, the version that appears on the FIS,
-- filed under Other
-- rather than under Income. Child support exhibits are built from the Income
-- parent, so the figure informs the financial picture without ever reaching a
-- support calculation. An off-FIS twin sits under Non-Income Investment
-- Activity for a matter where that is the wrong call -- the same pairing the
-- firm already uses for Credit Card Payments and Reimbursement.
--
-- Dividends, interest and capital gain distributions go the other way. Those
-- ARE net resources, and they belong under Income where the support exhibit
-- will find them.
--
-- Run after 036.

-- ── Repair: undo the earlier draft's mis-pointed rules ───────────────────────
--
-- Matched on the exact note strings that draft wrote, which no person would
-- type. A rule the firm authored is never touched.
delete from transaction_category_rules
where matter_id is null
  and note in (
      'Brokerage income',
      'Short form; low priority so a longer rule wins',
      'Sweep interest',
      'Fund distribution, not a realized sale gain',
      'Fund distribution',
      'Paid to the broker on borrowed money',
      'Brokerage fee',
      'Not a transaction; see 037 and the extractor',
      'Not a transaction',
      'In-kind transfer; names another account'
  );

-- Also clear this file's own rules, so re-running it picks up any category the
-- firm has since renamed or moved rather than leaving a stale target.
delete from transaction_category_rules
where matter_id is null and note like 'Seeded by 037%';

-- ── Categories, created only where the firm does not already have one ────────
--
-- Parents are resolved among TOP-LEVEL categories only. Descriptions are not
-- unique in this chart -- "Other", "Transportation", "Miscellaneous" and
-- "Insurance" all appear more than once -- and `parent_id is null` is what
-- makes the lookup unambiguous.
--
-- Interest Income and Brokerage Fees are deliberately absent from this list.
-- The firm already has both, and adding a second of either would split the same
-- money across two lines of a sworn statement.
insert into transaction_categories (description, parent_id, display_order, include_in_fis)
select v.description,
       (select c.id from transaction_categories c
         where c.description = v.parent and c.parent_id is null
         limit 1),
       v.display_order,
       v.include_in_fis
from (values
    -- Net resources. The support exhibit reads the Income subtree.
    ('Dividend Income',                   'Income',                          10, true),
    ('Capital Gain Distribution',         'Income',                          14, true),

    -- A real monthly cost of carrying debt, paid to a real creditor -- and the
    -- margin balance behind it is a liability that belongs in the
    -- undisclosed-accounts report as much as any card.
    ('Margin Interest',                   'Debt Payments',                 7005, true),

    -- THE DEFAULT: economic activity, on the FIS, outside Income.
    --
    -- Both of these ALREADY EXIST in the deployed chart (139 and 140), so the
    -- NOT EXISTS guard skips them and whatever parent and flag the firm set
    -- stands. They are listed anyway because a fresh database -- a rebuild, a
    -- second deployment -- has to end up with the same chart, and because the
    -- names here are what the rules below resolve against.
    ('Investment Gain/Loss',              'Other',                         8025, true),
    -- The twin, for a matter where counting it is the wrong call.
    ('Investment Gain/Loss (not on FIS)', 'Non-Income Investment Activity', 9025, false),

    -- Value moving between accounts without being earned or spent, exactly as
    -- Interaccount Transfers already is. An in-kind transfer also names another
    -- account, which is why account_discovery cares about it.
    ('Securities Transferred In',         'Non-Income Investment Activity', 9030, false),
    ('Securities Transferred Out',        'Non-Income Investment Activity', 9035, false)
) as v(description, parent, display_order, include_in_fis)
where not exists (
    select 1 from transaction_categories c
     where lower(btrim(c.description)) = lower(btrim(v.description))
)
  and exists (
    select 1 from transaction_categories p
     where p.description = v.parent and p.parent_id is null
);

-- The chart was seeded with explicit ids, so the sequence trails the data.
select setval(
    pg_get_serial_sequence('transaction_categories', 'id'),
    (select max(id) from transaction_categories)
);

-- ── Keyword rules, resolved by description ───────────────────────────────────
--
-- Brokerage descriptions are far more uniform than retail ones -- there is no
-- merchant, only a handful of phrases a clearing firm prints -- so a keyword
-- rule does more work here than anywhere else in the chart. Same guarantees as
-- every other rule (032): never overwrites a person, records what assigned it,
-- matched on word boundaries.
--
-- `applies_to` is 'credit' where amount >= 0 and 'debit' where amount < 0. It
-- is set wherever the same words could describe money moving the other way:
-- CAPITAL GAIN arriving is a distribution, CAPITAL GAIN leaving is a tax
-- payment, and one column prevents a confident mistake.
--
-- A rule whose category does not resolve is SKIPPED rather than inserted with a
-- null target. That is what makes the file safe to run against a chart that has
-- been reorganised: it does less, never something wrong.
insert into transaction_category_rules (matter_id, pattern, category_id, priority, applies_to, note)
select null,
       v.pattern,
       (select c.id from transaction_categories c
         where lower(btrim(c.description)) = lower(btrim(v.category))
         order by c.id limit 1),
       v.priority,
       v.applies_to,
       'Seeded by 037: ' || v.why
from (values
    ('QUALIFIED DIVIDEND',        'Dividend Income',           100, 'credit', 'brokerage income'),
    ('ORDINARY DIVIDEND',         'Dividend Income',           100, 'credit', 'brokerage income'),
    ('CASH DIVIDEND',             'Dividend Income',           100, 'credit', 'brokerage income'),
    ('DIVIDEND RECEIVED',         'Dividend Income',           100, 'credit', 'brokerage income'),
    ('DIVIDEND REINVESTMENT',     'Dividend Income',           100, 'credit',
        'the dividend added value; the purchase that spent it is not extracted'),
    -- Only ever matches a standalone DIV token: word boundaries mean it cannot
    -- match inside DIVIDEND, which the rules above take at higher priority.
    ('DIV',                       'Dividend Income',           400, 'credit',
        'short form some clearing firms print; low priority so a longer rule wins'),

    ('INTEREST INCOME',           'Interest Income',           100, 'credit', 'brokerage income'),
    ('CREDIT INTEREST',           'Interest Income',           100, 'credit', 'brokerage income'),
    ('BANK INTEREST',             'Interest Income',           100, 'credit', 'sweep interest'),

    ('CAPITAL GAIN',              'Capital Gain Distribution', 100, 'credit',
        'a fund distribution. Credit-only: CAPITAL GAIN leaving is a tax payment'),
    ('LONG TERM CAP GAIN',        'Capital Gain Distribution',  90, 'credit', 'fund distribution'),
    ('SHORT TERM CAP GAIN',       'Capital Gain Distribution',  90, 'credit', 'fund distribution'),

    ('MARGIN INTEREST',           'Margin Interest',           100, 'debit',
        'paid to the broker on money borrowed against the portfolio'),
    ('MARGIN INT',                'Margin Interest',           110, 'debit', 'short form'),

    ('ADVISORY FEE',              'Brokerage Fees',            100, 'debit', 'brokerage fee'),
    ('MANAGEMENT FEE',            'Brokerage Fees',            100, 'debit', 'brokerage fee'),
    -- Pointed at Bank Fees, not Brokerage Fees: this phrase appears on ordinary
    -- deposit statements far more often than on brokerage ones, and a rule is
    -- firm-wide across every account type.
    ('ACCOUNT FEE',               'Bank Fees',                 100, 'debit',
        'appears on deposit statements more often than brokerage ones'),

    -- The extractor writes the description "Change in Market Value"; statements
    -- print all three of these. Any of them lands on the FIS-visible category.
    ('CHANGE IN MARKET VALUE',    'Investment Gain/Loss',      100, 'any',
        'not a transaction -- the printed change in value. On the FIS by default'),
    ('UNREALIZED GAIN',           'Investment Gain/Loss',      100, 'any', 'as printed'),
    ('UNREALIZED LOSS',           'Investment Gain/Loss',      100, 'any', 'as printed'),
    ('MARKET APPRECIATION',       'Investment Gain/Loss',      100, 'any', 'as printed'),
    ('MARKET DEPRECIATION',       'Investment Gain/Loss',      100, 'any', 'as printed'),

    ('SECURITIES TRANSFERRED IN',  'Securities Transferred In',  100, 'any',
        'in-kind transfer; it names another account'),
    ('SECURITIES TRANSFERRED OUT', 'Securities Transferred Out', 100, 'any',
        'in-kind transfer; it names another account')
) as v(pattern, category, priority, applies_to, why)
where exists (
    select 1 from transaction_categories c
     where lower(btrim(c.description)) = lower(btrim(v.category))
)
on conflict do nothing;

-- ── Where the market-change figure is checked against ────────────────────────
--
-- The statement's own portfolio summary, kept beside the extraction rather than
-- folded into it. A brokerage statement states its own answer -- opening value,
-- additions, withdrawals, income, change in value, closing value -- and that
-- block is the second source the extraction is checked against, exactly as the
-- "24 Deposits/Credits 202,100.41" line is on a bank statement.
--
-- WHY A SECOND SOURCE IS NOT OPTIONAL HERE. Market change enters the ledger as
-- a line, so the ordinary check (beginning + sum == ending) closes whether or
-- not that line is right. A model that computed the plug instead of reading the
-- printed figure would produce a statement that reconciles perfectly and hides
-- a missing $50,000 wire. Comparing the extracted flows against the summary's
-- own additions and withdrawals is what catches that.
alter table financial_account_statements
    add column if not exists portfolio_summary jsonb;

comment on column financial_account_statements.portfolio_summary is
    'A brokerage statement''s own change-in-value block, as printed: opening_value, '
    'additions, withdrawals, income, change_in_market_value, closing_value. Kept as the '
    'independent check on an extraction that deliberately omits securities trades -- '
    'without it, the market-change line makes reconciliation close by construction.';

notify pgrst, 'reload schema';

-- ── After running: check what it did ─────────────────────────────────────────
--
-- Nothing below runs. Paste these once, before anybody trusts a report built on
-- the result.
--
-- 1. Every rule this file left behind, and what it actually points at. Read the
--    category column: that is the whole of what the earlier draft got wrong.
--
--      select r.pattern, r.applies_to, c.description as category,
--             p.description as parent, c.include_in_fis
--        from transaction_category_rules r
--        join transaction_categories c on c.id = r.category_id
--        left join transaction_categories p on p.id = c.parent_id
--       where r.matter_id is null and r.note like 'Seeded by 037%'
--       order by p.description, c.description, r.pattern;
--
-- 2. Whether an unrealized gain/loss category already existed under another
--    name, which would mean this file created a near-duplicate to merge:
--
--      select id, parent_id, description, include_in_fis
--        from transaction_categories
--       where description ilike '%unrealiz%'
--          or description ilike '%market value%'
--          or description ilike '%gain%loss%'
--          or description ilike '%investment%';
--
--    Expect exactly two rows -- 139 Investment Gain/Loss (on the FIS, under
--    Other) and 140 Investment Gain/Loss (not on FIS). A third means this file
--    created a duplicate under a name the firm does not use.

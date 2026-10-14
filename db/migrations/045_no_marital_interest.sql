-- 045_no_marital_interest.sql
--
-- A fifth characterization: the estate has no interest in this account at all.
--
-- RUN BY HAND, after 044. Safe to run twice.
--
-- ── WHY AN ACCOUNT NOBODY WILL DIVIDE BELONGS IN THE ACCOUNT LIST ────────────
--
-- The undisclosed-accounts report is built dynamically, every time, from the
-- transactions produced — which is right, and is what makes it impossible to
-- tick something off it. A $5 repayment to a sibling for parking puts that
-- sibling's account on the list, and it stays there on every run forever.
--
-- The way to get it off the list is not a second list of accounts to ignore,
-- kept in somebody's head or a spreadsheet. It is to put the account where
-- accounts live, with the reason recorded: `no_marital_interest`. The scan then
-- matches it like any other held account and stops reporting it, and the
-- judgment that it does not matter is a record somebody made rather than an
-- omission nobody can explain.
--
-- The same button adds an account the estate DOES have an interest in, with no
-- statements against it. That is the other half of the idea and the more
-- valuable one: an account on the matter with nothing produced is exactly what
-- the compliance grid is for, and it goes into the motion to compel.
--
-- ── WHAT THE VALUE MEANS, AND WHAT IT DOES NOT ──────────────────────────────
--
-- `no_marital_interest` says: whoever owns this, the marital estate does not,
-- so nobody needs to divide it or produce it. It is NOT "separate property" —
-- separate property is a party's own and still gets inventoried, which is why
-- `separate_petitioner` and `separate_respondent` already exist. This is a
-- third party's account that the production happened to name.
--
-- It is a characterization and therefore always a person's decision. Nothing
-- infers it, nothing defaults to it, and the two reports that read it say so:
-- such an account is left out of the compliance grid (there is nothing to
-- compel) and out of the inventory (the estate holds no interest to list).

begin;

alter table financial_accounts
    drop constraint if exists financial_accounts_property_character_check;

alter table financial_accounts
    add constraint financial_accounts_property_character_check
    check (property_character is null or property_character in (
        'community',
        'separate_petitioner',
        'separate_respondent',
        'mixed',
        'disputed',
        -- 045. A third party's account the production named in passing.
        'no_marital_interest'
    ));

commit;

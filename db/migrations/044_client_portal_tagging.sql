-- 044_client_portal_tagging.sql
--
-- The client portal: a tag a CLIENT applied, a tag staff CLEARED, which tags a
-- client may see, and a per-matter switch that stops client tagging during
-- hearing prep.
--
-- RUN BY HAND, after 043. Safe to run twice.
--
-- ── A CLIENT TAG IS NOT AN ATTORNEY JUDGMENT, AND THE RECORD MUST SAY SO ─────
--
-- 024 made `tagged_by_staff_id` NOT NULL with the comment "tagging is an
-- attorney judgment and gets cross-examined". That is still true of a staff
-- tag. A client working through two years of statements marking what they think
-- is waste is doing something different and genuinely useful, and the two must
-- never be indistinguishable: these tags drive Rule 1006 exhibits, and a
-- client's guess arriving in one as though an attorney had made it is the
-- confident-wrong failure this codebase spends its effort avoiding.
--
-- So the actor column splits in two, exactly one is set, and `source` says
-- which without a join.
--
-- ── CLEARING IS A SUPPRESSION, NOT A DELETE ──────────────────────────────────
--
-- Clients will tag things wrongly. Staff clear the tag -- and the client must
-- not be able to put it back, while staff can. A DELETE cannot express that:
-- the row would be gone and the next client click would recreate it. So the
-- link stays, carrying who cleared it and when, and the `(transaction, tag)`
-- unique constraint from 024 is what makes the second attempt land on the
-- cleared row instead of creating a new one.
--
-- Same shape as `transaction_payee_classifications`' `not_creditor` ruling and
-- the transaction soft delete: **suppression is always somebody's recorded
-- decision, never a silent default.** It also yields a number nothing else
-- would: a client cleared forty times is mis-tagging systematically, which is a
-- coaching signal staff would otherwise never see.

begin;

-- ── Who applied a tag ────────────────────────────────────────────────────────

alter table financial_account_transaction_tags
    alter column tagged_by_staff_id drop not null;

alter table financial_account_transaction_tags
    add column if not exists tagged_by_client_id integer references clients (id),
    add column if not exists source              text not null default 'staff',
    add column if not exists cleared_at          timestamptz,
    add column if not exists cleared_by_staff_id integer references staff (id);

do $$
begin
    if not exists (select 1 from pg_constraint
                    where conname = 'financial_account_transaction_tags_source') then
        alter table financial_account_transaction_tags
            add constraint financial_account_transaction_tags_source
            check (source in ('staff', 'client'));
    end if;
    -- Exactly one actor. A row claiming both, or neither, cannot say who made
    -- the assertion -- which is the only thing this table is for.
    if not exists (select 1 from pg_constraint
                    where conname = 'financial_account_transaction_tags_one_actor') then
        alter table financial_account_transaction_tags
            add constraint financial_account_transaction_tags_one_actor
            check (
                (source = 'staff'  and tagged_by_staff_id is not null
                                   and tagged_by_client_id is null)
                or
                (source = 'client' and tagged_by_client_id is not null
                                   and tagged_by_staff_id is null)
            );
    end if;
    -- Cleared by somebody, or not cleared. A timestamp with nobody attached
    -- reads as "the system did it", and nothing here is done by the system.
    if not exists (select 1 from pg_constraint
                    where conname = 'financial_account_transaction_tags_cleared') then
        alter table financial_account_transaction_tags
            add constraint financial_account_transaction_tags_cleared
            check ((cleared_at is null) = (cleared_by_staff_id is null));
    end if;
end $$;

-- Every row that existed before this migration was applied by staff, which the
-- default already says; this states it for rows written while the deploy was
-- mid-flight.
update financial_account_transaction_tags
   set source = 'staff'
 where source is null or (source = 'client' and tagged_by_client_id is null);

-- "Which lines did this client tag, and what has been cleared" — the two
-- questions the portal and the review screen ask.
create index if not exists idx_transaction_tags_by_client
    on financial_account_transaction_tags (tagged_by_client_id, created_at)
    where tagged_by_client_id is not null;

create index if not exists idx_transaction_tags_cleared
    on financial_account_transaction_tags (cleared_at)
    where cleared_at is not null;

-- ── Which tags a client may see ──────────────────────────────────────────────
--
-- Opt-IN, deliberately. The firm's tag list carries litigation strategy
-- ("Reconstitute - paramour", "Ask expert"), and defaulting to visible would
-- publish all of it to the client the day the portal ships.

alter table transaction_tags
    add column if not exists client_visible boolean not null default false;

-- ── The hearing-prep lock ────────────────────────────────────────────────────
--
-- Tags go into exhibits. Once a matter is being prepared for trial the set has
-- to stop moving, and the switch is per matter because that is how hearings
-- happen. Staff tagging is unaffected: the lock is about the client, not the
-- matter.
--
-- Defaults to FALSE: a matter does not acquire a tagging client merely because
-- this migration ran. Staff turn it on per matter, which is also the moment
-- somebody decides the client should be in there at all.

alter table matters
    add column if not exists client_tagging_enabled boolean not null default false;

-- ── A starter set of waste-claim tags ────────────────────────────────────────
--
-- Firm-wide, client-visible, and seeded by LABEL with a NOT EXISTS guard so a
-- firm that already uses these names keeps its own rows (024 seeded by explicit
-- id and the firm has edited the list since; see CLAUDE.md §15).
--
-- These are the categories a reconstituted-estate claim is actually pleaded in,
-- which is the point: a client marking "gifts to a third party" is doing the
-- first pass of work somebody has to do anyway. Nothing is suppressed by a tag,
-- so a seeded one that nobody uses costs a line in a list.

insert into transaction_tags (matter_id, label, description, color, display_order, client_visible)
select null, v.label, v.description, v.color, v.display_order, true
  from (values
    ('Client: possible waste',      'Money you believe was spent wastefully. Your attorney will review each one.', 'rose',   900),
    ('Client: gift to someone else','A gift or payment to someone outside the household.',                        'rose',   901),
    ('Client: gambling',            'Casino, lottery, sports betting or similar.',                                'rose',   902),
    ('Client: travel',              'Trips you did not take, or did not know about.',                             'amber',  903),
    ('Client: large cash',          'Cash withdrawals you cannot account for.',                                   'amber',  904),
    ('Client: unknown to me',       'You do not recognise this transaction at all.',                              'slate',  905),
    ('Client: ask me about this',   'Something you want to discuss with your attorney.',                          'sky',    906)
  ) as v(label, description, color, display_order)
 where not exists (
     select 1 from transaction_tags t
      where t.matter_id is null and lower(t.label) = lower(v.label));

commit;

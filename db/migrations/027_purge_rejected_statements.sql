-- ═══════════════════════════════════════════════════════════════════════════
-- 027_purge_rejected_statements.sql
--
-- ⚠ THIS MIGRATION DELETES DATA. Read the note before running it.
--
-- Rejecting a statement used to flip `review_status` and stop there. The
-- statement stayed, its transactions stayed, and the account the bad import had
-- created stayed — all of them filtered out of every view. That is the worst of
-- both worlds: invisible, so nobody can act on them, but still present, so an
-- empty account clutters the inventory forever.
--
-- Rejection now deletes. This migration applies the same rule to the rows that
-- were rejected under the old behaviour.
--
-- Run the SELECT first. If any of those statements are ones you meant to keep,
-- set them back to 'accepted' before running the DELETE.
--
-- Run after 026.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Look before you delete ───────────────────────────────────────────────
-- What would go, and how many lines with it.
select s.id,
       s.matter_id,
       a.institution,
       a.account_number_last4,
       s.period_start,
       s.period_end,
       s.extraction ->> 'source_filename' as source_filename,
       (select count(*) from financial_account_transactions t where t.statement_id = s.id)
           as transactions_to_delete
  from financial_account_statements s
  join financial_accounts a on a.id = s.financial_account_id
 where s.review_status = 'rejected'
 order by s.matter_id, a.institution, s.period_start;

-- ── The delete ───────────────────────────────────────────────────────────
-- Transactions go with the statement: financial_account_transactions cascades
-- on the composite FK to its parent, and the transaction⇄tag links cascade in
-- turn. Nothing else has to be named here.
delete from financial_account_statements
 where review_status = 'rejected';

-- ── Accounts left with nothing ───────────────────────────────────────────
-- Only the ones the application itself would have removed: no statements, no
-- place in an account history, and no judgment recorded on them.
-- Characterization, ownership, purpose, and notes are attorney work and outlive
-- the import that happened to create the row.
delete from financial_accounts a
 where not exists (select 1 from financial_account_statements s
                    where s.financial_account_id = a.id)
   and not exists (select 1 from financial_accounts other
                    where other.antecedent_account_id = a.id)
   and a.antecedent_account_id is null
   and a.ownership = 'unknown'
   and a.property_character is null
   and coalesce(nullif(trim(a.purpose), ''), '') = ''
   and coalesce(nullif(trim(a.notes), ''), '') = '';

-- The source PDFs are deliberately left in Storage. One upload can back several
-- statements, so deleting a file on one rejection would take another's source
-- with it, and the jobs row still names the path either way.

notify pgrst, 'reload schema';

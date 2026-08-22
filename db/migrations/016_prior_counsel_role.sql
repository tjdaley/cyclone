-- 016_prior_counsel_role.sql
-- Adds 'prior_counsel' to the matter↔counsel role vocabulary.
--
-- When opposing counsel substitutes out, the earlier attorney should stay on the
-- matter rather than being deleted: their filings are part of the record, and we
-- may still need their contact details. Marking them 'prior_counsel' keeps the
-- history while sorting them out of the way of whoever is currently handling
-- the case.
--
-- The role column already carries a CHECK constraint (009_pleadings_and_oc.sql),
-- which has to be dropped and recreated to widen the allowed set.

alter table matter_opposing_counsel
    drop constraint if exists matter_opposing_counsel_role_check;

alter table matter_opposing_counsel
    add constraint matter_opposing_counsel_role_check
    check (role in ('lead', 'co_counsel', 'local_counsel', 'prior_counsel'));

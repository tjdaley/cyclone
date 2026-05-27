-- 011_lead_dismissal_note.sql
-- Adds free-text detail for lead disqualifications.
--
-- leads_workflow.dismissal_reason already exists (010_crm.sql) and now stores
-- a normalized DismissalReason code (subject_matter | income | spam | other).
-- This column holds optional free text, primarily for the 'other' reason.
--
-- No change to the lead_status enum is needed: 010_crm.sql created it with a
-- single 'disqualified' value, which is what we use. The "why" is captured by
-- dismissal_reason / dismissal_note, not by additional status values.

alter table leads_workflow add column if not exists dismissal_note text;

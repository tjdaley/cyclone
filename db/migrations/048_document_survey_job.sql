-- 048_document_survey_job.sql — surveying a received file happens in a worker
--
-- RUN BY HAND, after 047.
--
-- ── WHY THIS EXISTS ──────────────────────────────────────────────────────────
--
-- Reading a received file to find out what it is — how many pages carry text,
-- how many describe their own pagination — used to happen inside the upload
-- request. On a 74 MB production that request was already moving the bytes
-- twice (browser to the API, API to Storage), and haproxy cut it with a 504
-- while everything was still working. The same lesson as §11a, met again:
-- anything that can take more than a few seconds must not hold a request open.
--
-- The upload now goes BROWSER TO STORAGE DIRECTLY, over a signed URL, so the
-- bytes never touch the API at all. What remains is reading the file, and that
-- becomes a job like every other piece of minutes-long work here.
--
-- `document_survey` computes `source_documents.page_features` once. The routing
-- verdict is DERIVED from those features rather than stored beside them —
-- text share and pagination coverage are both computable from the per-page
-- facts, and a second copy of an answer is the drift this codebase keeps
-- meeting.
--
-- No new table, so no RLS clause is needed; tests/test_migration_rls.py checks
-- that claim.

begin;

alter table jobs drop constraint if exists jobs_kind_check;
alter table jobs
    add constraint jobs_kind_check
    check (kind in ('matter_intake', 'statement_ingest', 'document_survey'));

-- A survey has no matter-scoped input of its own: it is about one received
-- file, named in params.source_document_id. `matter_id` is still set, because
-- every job here is scoped to a matter for the queue and for attribution.

commit;

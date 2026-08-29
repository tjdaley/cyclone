-- ═══════════════════════════════════════════════════════════════════════════
-- 025_job_params.sql
--
-- Input options for a queued job.
--
-- `jobs` already carries `result` for what a job produces, but nothing for the
-- options it was started with. The first case is the Bates prefix override on a
-- statement upload: the user types it at the upload, and the worker needs it
-- minutes later in a different process.
--
-- jsonb rather than a column per option — these are per-kind and there will be
-- more of them (an OCR hint, a re-extract flag), and none of them are ever
-- queried on.
--
-- Run after 024.
-- ═══════════════════════════════════════════════════════════════════════════

alter table jobs
    add column if not exists params jsonb not null default '{}'::jsonb;

notify pgrst, 'reload schema';

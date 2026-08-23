-- 018_jobs.sql
-- Background jobs for work that is too slow to hold an HTTP request open.
--
-- Matter intake reads a PDF (one LLM vision call per image-only page) and then
-- runs two more LLM calls. On a scanned pleading that is minutes of work, which
-- no reverse-proxy timeout will tolerate — the upload returned a 504 from
-- haproxy while the API was still working, with nothing logged because nothing
-- had failed.
--
-- The upload now stores the PDF, inserts a queued row here, and returns the id.
-- The worker claims the row, runs the extraction, and writes `result`. The
-- review screen polls until the status is terminal.
--
-- Rows are kept after completion: `result` is what the review screen renders,
-- and a failed job's `error` is the only record of why an upload went nowhere.

create table if not exists jobs (
    id                    uuid        primary key default gen_random_uuid(),
    kind                  text        not null
                              check (kind in ('matter_intake')),
    status                text        not null default 'queued'
                              check (status in ('queued', 'running', 'succeeded', 'failed')),
    storage_path          text,
    requested_by_staff_id integer     not null references staff (id),
    result                jsonb,
    error                 text,
    attempts              integer     not null default 0,
    created_at            timestamptz not null default now(),
    started_at            timestamptz,
    finished_at           timestamptz,
    updated_at            timestamptz
);

-- The worker's only query: oldest queued job of a kind.
create index if not exists idx_jobs_status_created on jobs (status, created_at);

-- Callers poll their own jobs.
create index if not exists idx_jobs_requested_by on jobs (requested_by_staff_id, created_at desc);

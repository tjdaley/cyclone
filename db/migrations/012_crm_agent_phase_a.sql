-- 012_crm_agent_phase_a.sql
-- Phase A foundations for the CRM email agent: responder routing, the agent
-- run trace table, and inbound-email idempotency.

-- ── attorney_lead_responders ────────────────────────────────────────────────
-- Many-to-many: which support staff respond to which attorney's PNC leads.
-- Both sides are staff rows; the attorney side should have role='attorney' and
-- the responder side role<>'attorney' (enforced in the app layer, not the FK,
-- since roles can change). The slug='www' "Firm" staff record holds the
-- firm-wide responders for unattributed / unassigned leads.
create table attorney_lead_responders (
    id bigserial primary key,
    attorney_staff_id  integer not null references staff(id) on delete cascade,
    responder_staff_id integer not null references staff(id) on delete cascade,
    created_at timestamptz default now() not null,
    unique (attorney_staff_id, responder_staff_id)
);
create index idx_alr_attorney  on attorney_lead_responders(attorney_staff_id);
create index idx_alr_responder on attorney_lead_responders(responder_staff_id);

-- ── lead_agent_runs ─────────────────────────────────────────────────────────
-- One row per agent invocation (a welcome send, or processing an inbound
-- reply). Captures the pipeline trace plus the human-in-the-loop eval data
-- (draft vs sent, whether a human edited it). In Phase A only 'welcome' runs
-- are written; later phases populate triage / issues / dispositions.
create table lead_agent_runs (
    id bigserial primary key,
    foreign_session_uuid uuid not null,
    trigger text not null,                       -- 'welcome' | 'inbound_reply'
    inbound_message_id text,                     -- Message-ID that triggered the run (null for welcome)
    triage_result text,                          -- 'spam' | 'escalate' | 'continue'
    issues jsonb not null default '[]'::jsonb,
    dispositions jsonb not null default '[]'::jsonb,
    draft_body text,                             -- what the agent generated
    sent_body text,                              -- what actually went out (may be human-edited)
    human_edited boolean not null default false,
    edit_explanation text,                       -- filled lazily by the diff-explainer batch job
    guardrail_passed boolean,
    final_action text,                           -- 'welcome_sent' | 'sent' | 'drafted_pending_approval' | 'escalated' | 'spam_filed' | 'error'
    status text not null default 'running',      -- 'running' | 'awaiting_approval' | 'done' | 'error'
    error text,
    created_at timestamptz default now() not null,
    updated_at timestamptz default now() not null
);
create index idx_agent_runs_session on lead_agent_runs(foreign_session_uuid, created_at desc);
create index idx_agent_runs_status  on lead_agent_runs(status);

create trigger trg_lead_agent_runs_updated_at
    before update on lead_agent_runs
    for each row execute function set_updated_at();

-- ── processed_inbound_emails ────────────────────────────────────────────────
-- Durable idempotency backstop for the inbound poller. Redis holds the fast
-- claim lock; this table survives a Redis flush so a message is never
-- reprocessed even if the cache is lost.
create table processed_inbound_emails (
    message_id text primary key,
    foreign_session_uuid uuid,
    processed_at timestamptz default now() not null
);

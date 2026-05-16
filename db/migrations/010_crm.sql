-- 010_crm.sql
-- CRM tables for lead management.
--
-- Source of truth for lead rows themselves is the landing-pages Supabase
-- project. This migration adds cyclone-side workflow state, an append-only
-- activity log, per-staff access control by attorney slug, and a couple of
-- staff columns the AI agent will need to respond to leads.

-- ── Enums ──────────────────────────────────────────────────────────────────

create type lead_status as enum (
    'new',
    'attempted',
    'contacted',
    'qualified',
    'disqualified',
    'consultation_scheduled',
    'consulted',
    'engaged',
    'lost',
    'nurture'
);

create type lead_actor_type as enum (
    'staff',
    'ai_agent',
    'system'
);

create type lead_action_direction as enum (
    'outbound',
    'inbound',
    'internal'
);

create type lead_action_type as enum (
    'call_attempted',
    'call_connected',
    'voicemail_left',
    'email_sent',
    'email_received',
    'text_sent',
    'text_received',
    'note',
    'status_change',
    'assigned',
    'priority_change',
    'consultation_scheduled',
    'consultation_held',
    'conflict_check_run',
    'converted',
    'agent_escalated',
    'follow_up_set'
);

-- ── staff additions for AI agent ──────────────────────────────────────────

alter table staff add column if not exists calendly_url text;
alter table staff add column if not exists agent_signature text;

-- ── staff_slug_access ─────────────────────────────────────────────────────
-- Per-staff allowlist of attorney slugs the staff member can see leads for.
-- A slug value of '*' = wildcard (see all slugs).
-- Admin role (resolved at the FastAPI route layer) implicitly bypasses
-- this table and sees everything.

create table staff_slug_access (
    id bigserial primary key,
    staff_id integer not null references staff(id) on delete cascade,
    slug text not null,
    created_at timestamptz default now() not null,
    unique (staff_id, slug)
);

create index idx_staff_slug_access_staff on staff_slug_access(staff_id);
create index idx_staff_slug_access_slug on staff_slug_access(slug);

-- ── leads_workflow ────────────────────────────────────────────────────────
-- One row per lead, owns all CRM workflow state.
-- Lazily created on first touch (view, status update, assignment, etc.) —
-- not every foreign lead has a row until cyclone has interacted with it.

create table leads_workflow (
    id bigserial primary key,
    foreign_lead_id integer not null,
    foreign_session_uuid uuid not null unique,
    attorney_slug text not null,
    status lead_status not null default 'new',
    assigned_staff_id integer references staff(id),
    priority text not null default 'normal',
    next_action_at timestamptz,
    next_action_note text,
    dismissal_reason text,
    converted_to_client_id integer references clients(id),
    converted_to_matter_id integer references matters(id),
    agent_enabled boolean not null default false,
    agent_summary text,
    agent_last_run_at timestamptz,
    agent_handoff_reason text,
    created_at timestamptz default now() not null,
    updated_at timestamptz default now() not null
);

create index idx_leads_workflow_session_uuid on leads_workflow(foreign_session_uuid);
create index idx_leads_workflow_slug_status on leads_workflow(attorney_slug, status);
create index idx_leads_workflow_assigned on leads_workflow(assigned_staff_id);
create index idx_leads_workflow_next_action on leads_workflow(next_action_at) where next_action_at is not null;

create trigger trg_leads_workflow_updated_at
    before update on leads_workflow
    for each row execute function set_updated_at();

-- ── lead_actions ──────────────────────────────────────────────────────────
-- Append-only activity log. Every state change, note, message, and
-- AI agent action lands here.

create table lead_actions (
    id bigserial primary key,
    foreign_session_uuid uuid not null,
    actor_type lead_actor_type not null default 'staff',
    staff_id integer references staff(id),
    action_type lead_action_type not null,
    direction lead_action_direction not null default 'internal',
    body text,
    notes text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz default now() not null
);

create index idx_lead_actions_session_uuid on lead_actions(foreign_session_uuid, created_at desc);
create index idx_lead_actions_staff on lead_actions(staff_id);
create index idx_lead_actions_type on lead_actions(action_type);

-- Append-only enforcement
create or replace function deny_lead_action_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'lead_actions is append-only — no updates or deletes';
end;
$$;

create trigger trg_lead_actions_no_update
    before update on lead_actions
    for each row execute function deny_lead_action_mutation();

create trigger trg_lead_actions_no_delete
    before delete on lead_actions
    for each row execute function deny_lead_action_mutation();

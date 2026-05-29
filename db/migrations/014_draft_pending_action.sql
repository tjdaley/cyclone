-- 014_draft_pending_action.sql
-- Phase C.2: a new lead_action_type for AI drafts awaiting human approval.
--
-- A draft_pending row's `body` carries the AI-generated reply text; its
-- metadata.run_id points at the lead_agent_runs row holding the full trace
-- (issues, guardrail verdict, retrieval notes). C.3's HITL UI binds Send /
-- Reject actions to that run.

alter type lead_action_type add value if not exists 'draft_pending';

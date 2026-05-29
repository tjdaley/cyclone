/** Mirrors LeadAgentRunResponse in app/schemas/lead_agent_run.py */
export interface LeadAgentRun {
  id: number
  foreign_session_uuid: string
  trigger: string
  triage_result: string | null
  draft_body: string | null
  sent_body: string | null
  human_edited: boolean
  guardrail_passed: boolean | null
  final_action: string | null
  status: string
  created_at: string
  updated_at: string | null
}

export interface SendDraftPayload {
  body: string
}

export interface RejectDraftPayload {
  reason?: string
}

/** Mirrors EditedRunSummary in app/schemas/lead_agent_run.py */
export interface EditedRunSummary {
  id: number
  foreign_session_uuid: string
  lead_name: string | null
  lead_email: string | null
  draft_body: string
  sent_body: string
  edit_explanation: string | null
  updated_at: string | null
}

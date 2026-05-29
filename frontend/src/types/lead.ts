/** Mirrors LeadStatus enum in app/db/models/lead_workflow.py */
export type LeadStatus =
  | 'new'
  | 'attempted'
  | 'contacted'
  | 'qualified'
  | 'disqualified'
  | 'consultation_scheduled'
  | 'consulted'
  | 'engaged'
  | 'lost'
  | 'nurture'

/** Mirrors DismissalReason enum in app/db/models/lead_workflow.py */
export type DismissalReason = 'subject_matter' | 'income' | 'spam' | 'other'

/** Mirrors LeadPriority enum in app/db/models/lead_workflow.py */
export type LeadPriority = 'low' | 'normal' | 'high'

/** Mirrors LeadActorType in app/db/models/lead_action.py */
export type LeadActorType = 'staff' | 'ai_agent' | 'system'

/** Mirrors LeadActionDirection in app/db/models/lead_action.py */
export type LeadActionDirection = 'outbound' | 'inbound' | 'internal'

/** Mirrors LeadActionType in app/db/models/lead_action.py */
export type LeadActionType =
  | 'call_attempted'
  | 'call_connected'
  | 'voicemail_left'
  | 'email_sent'
  | 'email_received'
  | 'text_sent'
  | 'text_received'
  | 'note'
  | 'status_change'
  | 'assigned'
  | 'priority_change'
  | 'consultation_scheduled'
  | 'consultation_held'
  | 'conflict_check_run'
  | 'converted'
  | 'agent_escalated'
  | 'follow_up_set'
  | 'draft_pending'

/** Mirrors LeadListItem in app/schemas/lead.py */
export interface LeadListItem {
  session_uuid: string
  foreign_lead_id: number
  attorney_slug: string | null
  full_name: string | null
  email: string | null
  telephone: string | null
  audit_score: number | null
  lead_source: string | null
  state: string | null
  city: string | null
  captured_at: string
  status: LeadStatus
  assigned_staff_id: number | null
  priority: LeadPriority
  next_action_at: string | null
  has_workflow_row: boolean
}

/** Mirrors LeadDetail in app/schemas/lead.py */
export interface LeadDetail {
  session_uuid: string
  foreign_lead_id: number
  attorney_slug: string | null
  captured_at: string
  full_name: string | null
  email: string | null
  telephone: string | null
  audit_score: number | null
  country: string | null
  state: string | null
  city: string | null
  zip: string | null
  url_path: string | null
  lead_source: string | null
  referrer: string | null
  conflict_summary: string | null
  status: LeadStatus
  assigned_staff_id: number | null
  priority: LeadPriority
  next_action_at: string | null
  next_action_note: string | null
  dismissal_reason: DismissalReason | null
  dismissal_note: string | null
  converted_to_client_id: number | null
  converted_to_matter_id: number | null
  agent_enabled: boolean
  agent_summary: string | null
  agent_last_run_at: string | null
  agent_handoff_reason: string | null
}

/** Mirrors LeadActionResponse in app/schemas/lead.py */
export interface LeadAction {
  id: number
  session_uuid: string
  actor_type: LeadActorType
  staff_id: number | null
  action_type: LeadActionType
  direction: LeadActionDirection
  body: string | null
  notes: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface StatusUpdatePayload {
  status: LeadStatus
  dismissal_reason?: DismissalReason | null
  dismissal_note?: string | null
}

export interface AssignPayload {
  staff_id: number | null
}

export interface PriorityUpdatePayload {
  priority: LeadPriority
}

export interface FollowUpPayload {
  next_action_at: string | null
  next_action_note?: string | null
}

export interface AgentTogglePayload {
  agent_enabled: boolean
}

export interface AddNotePayload {
  body: string
}

export interface AddActionPayload {
  action_type: LeadActionType
  direction?: LeadActionDirection
  body?: string | null
  notes?: string | null
  metadata?: Record<string, unknown>
}

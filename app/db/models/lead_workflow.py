"""
app/db/models/lead_workflow.py - Cyclone-side workflow state for leads.

Lead rows themselves live in the landing-pages Supabase project (read-only
from cyclone's perspective). This model represents the per-lead workflow
state that cyclone manages: status, assignment, follow-up reminders,
conversion targets, and AI agent state.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Values must match the lead_status Postgres enum (db/migrations/010_crm.sql)
# and the LeadStatus union in frontend/src/types/lead.ts.
class LeadStatus(str, Enum):
    """Pipeline stage for a lead. The *why* of a disqualification lives in
    DismissalReason, not here — keep this enum about pipeline position only."""
    new = "new"
    attempted = "attempted"
    contacted = "contacted"
    qualified = "qualified"
    disqualified = "disqualified"
    consultation_scheduled = "consultation_scheduled"
    consulted = "consulted"
    engaged = "engaged"
    lost = "lost"
    nurture = "nurture"


class DismissalReason(str, Enum):
    """Normalized reason a lead was disqualified. Stored as a stable code in
    leads_workflow.dismissal_reason so reasons can be aggregated
    (GROUP BY dismissal_reason) without parsing free text. The 'other' case
    is accompanied by free text in dismissal_note."""
    subject_matter = "subject_matter"
    income = "income"
    spam = "spam"
    other = "other"


class LeadPriority(str, Enum):
    """Coarse priority for triage."""
    low = "low"
    normal = "normal"
    high = "high"


class LeadWorkflow(BaseModel):
    """Domain model for the cyclone-side workflow state of a single lead."""
    foreign_lead_id: int = Field(..., description="Primary key of the lead in the landing-pages DB")
    foreign_session_uuid: UUID = Field(..., description="Stable cross-DB identifier for this lead")
    attorney_slug: str = Field(..., description="Denormalized from the foreign lead row for access filtering")
    status: LeadStatus = Field(default=LeadStatus.new, description="Pipeline stage")
    assigned_staff_id: Optional[int] = Field(default=None, description="Staff member currently responsible for this lead")
    priority: LeadPriority = Field(default=LeadPriority.normal, description="Triage priority")
    next_action_at: Optional[datetime] = Field(default=None, description="When the assigned staff member should follow up")
    next_action_note: Optional[str] = Field(default=None, description="Short description of the next action")
    dismissal_reason: Optional[DismissalReason] = Field(default=None, description="Normalized reason for disqualification, set when status=disqualified")
    dismissal_note: Optional[str] = Field(default=None, description="Free-text detail for the disqualification, primarily for the 'other' reason")
    converted_to_client_id: Optional[int] = Field(default=None, description="Client record created when the lead engaged")
    converted_to_matter_id: Optional[int] = Field(default=None, description="Matter record created when the lead engaged")
    agent_enabled: bool = Field(default=False, description="If true, the AI agent will respond to inbound messages on this lead")
    agent_summary: Optional[str] = Field(default=None, description="Evolving summary the agent updates after each turn; used as context for subsequent prompts")
    agent_last_run_at: Optional[datetime] = Field(default=None, description="Timestamp of the agent's last action on this lead")
    agent_handoff_reason: Optional[str] = Field(default=None, description="Reason the agent escalated to a human; null while agent is still handling")


class LeadWorkflowInDB(LeadWorkflow):
    """Database model — adds DB-managed fields."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by the database on update")
    model_config = ConfigDict(from_attributes=True)

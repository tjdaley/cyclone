"""
app/db/models/lead_action.py - Append-only activity log for leads.

Every state change, note, message, and AI agent action lands here.
Enforced append-only by DB trigger (see db/migrations/010_crm.sql).
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadActorType(str, Enum):
    """Who performed the action."""
    staff = "staff"
    ai_agent = "ai_agent"
    system = "system"


class LeadActionDirection(str, Enum):
    """Direction of a message-bearing action; 'internal' for non-message actions."""
    outbound = "outbound"
    inbound = "inbound"
    internal = "internal"


class LeadActionType(str, Enum):
    """What happened. New values must be added to the matching SQL enum too."""
    call_attempted = "call_attempted"
    call_connected = "call_connected"
    voicemail_left = "voicemail_left"
    email_sent = "email_sent"
    email_received = "email_received"
    text_sent = "text_sent"
    text_received = "text_received"
    note = "note"
    status_change = "status_change"
    assigned = "assigned"
    priority_change = "priority_change"
    consultation_scheduled = "consultation_scheduled"
    consultation_held = "consultation_held"
    conflict_check_run = "conflict_check_run"
    converted = "converted"
    agent_escalated = "agent_escalated"
    follow_up_set = "follow_up_set"
    draft_pending = "draft_pending"


class LeadAction(BaseModel):
    """Domain model for an entry in the lead activity log."""
    foreign_session_uuid: UUID = Field(..., description="Which lead this action belongs to")
    actor_type: LeadActorType = Field(default=LeadActorType.staff, description="Who performed the action")
    staff_id: Optional[int] = Field(default=None, description="Staff member who performed the action; null when actor_type is ai_agent or system")
    action_type: LeadActionType = Field(..., description="What happened")
    direction: LeadActionDirection = Field(default=LeadActionDirection.internal, description="Inbound, outbound, or internal (non-message)")
    body: Optional[str] = Field(default=None, description="Verbatim message content for email/text actions; null otherwise")
    notes: Optional[str] = Field(default=None, description="Internal annotations not shown to the lead")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Free-form structured data: thread_id, message_id, model used, confidence, etc.")


class LeadActionInDB(LeadAction):
    """Database model — append-only, so no updated_at."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    model_config = ConfigDict(from_attributes=True)

"""
app/schemas/lead.py - Request and response schemas for the CRM endpoints.

LeadListItem and LeadDetail are denormalized payloads that merge fields from
the foreign landing-pages.leads row with the cyclone-side leads_workflow row,
so the frontend can render everything from one response.
"""
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from db.models.lead_action import LeadActionDirection, LeadActionType, LeadActorType
from db.models.lead_workflow import LeadPriority, LeadStatus


# ── Lead list / detail ────────────────────────────────────────────────────

class LeadListItem(BaseModel):
    """One row in the leads list view. Lightweight — full body of conflict_summary is omitted."""
    session_uuid: UUID
    foreign_lead_id: int
    attorney_slug: Optional[str]
    full_name: Optional[str]
    email: Optional[str]
    telephone: Optional[str]
    audit_score: Optional[int]
    lead_source: Optional[str]
    state: Optional[str]
    city: Optional[str]
    captured_at: datetime = Field(..., description="When the lead was captured (foreign created_at)")
    status: LeadStatus = Field(..., description="Defaults to 'new' when no workflow row exists yet")
    assigned_staff_id: Optional[int] = None
    priority: LeadPriority = LeadPriority.normal
    next_action_at: Optional[datetime] = None
    has_workflow_row: bool = Field(..., description="False until cyclone has interacted with this lead")


class LeadDetail(BaseModel):
    """Full lead detail: foreign data + workflow state."""
    session_uuid: UUID
    foreign_lead_id: int
    attorney_slug: Optional[str]
    captured_at: datetime
    full_name: Optional[str]
    email: Optional[str]
    telephone: Optional[str]
    audit_score: Optional[int]
    country: Optional[str]
    state: Optional[str]
    city: Optional[str]
    zip: Optional[str]
    url_path: Optional[str]
    lead_source: Optional[str]
    referrer: Optional[str]
    conflict_summary: Optional[str]

    status: LeadStatus
    assigned_staff_id: Optional[int]
    priority: LeadPriority
    next_action_at: Optional[datetime]
    next_action_note: Optional[str]
    dismissal_reason: Optional[str]
    converted_to_client_id: Optional[int]
    converted_to_matter_id: Optional[int]
    agent_enabled: bool
    agent_summary: Optional[str]
    agent_last_run_at: Optional[datetime]
    agent_handoff_reason: Optional[str]


# ── Actions / activity log ────────────────────────────────────────────────

class LeadActionResponse(BaseModel):
    """One entry in the activity timeline."""
    id: int
    session_uuid: UUID
    actor_type: LeadActorType
    staff_id: Optional[int]
    action_type: LeadActionType
    direction: LeadActionDirection
    body: Optional[str]
    notes: Optional[str]
    metadata: dict[str, Any]
    created_at: datetime


# ── Mutation requests ─────────────────────────────────────────────────────

class StatusUpdateRequest(BaseModel):
    status: LeadStatus
    dismissal_reason: Optional[str] = Field(default=None, description="Required when status='disqualified'")


class AssignRequest(BaseModel):
    staff_id: Optional[int] = Field(default=None, description="Null to unassign")


class PriorityUpdateRequest(BaseModel):
    priority: LeadPriority


class FollowUpRequest(BaseModel):
    next_action_at: Optional[datetime] = Field(default=None, description="Null clears the follow-up")
    next_action_note: Optional[str] = None


class AgentToggleRequest(BaseModel):
    agent_enabled: bool


class AddNoteRequest(BaseModel):
    body: str = Field(..., description="The note text")


class AddActionRequest(BaseModel):
    """Generic action logger for manual events like 'call attempted'."""
    action_type: LeadActionType
    direction: LeadActionDirection = LeadActionDirection.internal
    body: Optional[str] = None
    notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Admin: staff_slug_access ──────────────────────────────────────────────

class SlugAccessGrant(BaseModel):
    """Body for granting slug access to a staff member."""
    staff_id: int
    slug: str = Field(..., description="Use '*' for wildcard access")


class SlugAccessResponse(BaseModel):
    id: int
    staff_id: int
    slug: str
    created_at: datetime

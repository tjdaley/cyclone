"""
app/schemas/lead_agent_run.py - Request/response schemas for HITL approval endpoints.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SendDraftRequest(BaseModel):
    """Body for POST /api/v1/lead-agent-runs/{id}/send.

    The ``body`` is the FINAL text that goes out to the PNC. If it differs from
    ``draft_body`` on the run row, the run is marked ``human_edited=true``.
    """
    body: str = Field(..., description="Final email body to send to the PNC")


class RejectDraftRequest(BaseModel):
    """Body for POST /api/v1/lead-agent-runs/{id}/reject."""
    reason: Optional[str] = Field(
        default=None,
        description="Optional internal note explaining the rejection (audit trail)",
    )


class LeadAgentRunResponse(BaseModel):
    """Slimmed-down response shape for the run row after a HITL action."""
    id: int
    foreign_session_uuid: UUID
    trigger: str
    triage_result: Optional[str]
    draft_body: Optional[str]
    sent_body: Optional[str]
    human_edited: bool
    guardrail_passed: Optional[bool]
    final_action: Optional[str]
    status: str
    created_at: datetime
    updated_at: Optional[datetime]


class EditedRunSummary(BaseModel):
    """Run + lead-name pair for the 'recent draft edits' admin review."""
    id: int
    foreign_session_uuid: UUID
    lead_name: Optional[str]
    lead_email: Optional[str]
    draft_body: str
    sent_body: str
    edit_explanation: Optional[str]
    updated_at: Optional[datetime]

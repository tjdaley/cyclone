"""
app/db/models/lead_agent_run.py - One row per CRM agent invocation.

Captures the pipeline trace (triage, issues, dispositions) plus the
human-in-the-loop eval data (draft vs sent, whether a human edited it).
In Phase A only 'welcome' runs are written; later phases populate the rest.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadAgentTrigger(str, Enum):
    """What caused this run."""
    welcome = "welcome"
    inbound_reply = "inbound_reply"


class LeadAgentRun(BaseModel):
    """Domain model for an agent run."""
    foreign_session_uuid: UUID = Field(..., description="Which lead this run is about")
    trigger: LeadAgentTrigger = Field(..., description="What kicked off the run")
    inbound_message_id: Optional[str] = Field(default=None, description="Message-ID that triggered the run; null for welcome runs")
    triage_result: Optional[str] = Field(default=None, description="'spam' | 'escalate' | 'continue'; null until triage runs")
    issues: list[Any] = Field(default_factory=list, description="Issues extracted from the inbound email")
    dispositions: list[Any] = Field(default_factory=list, description="Per-issue outcome (answered / escalated)")
    draft_body: Optional[str] = Field(default=None, description="The message the agent generated")
    sent_body: Optional[str] = Field(default=None, description="The message actually sent (may differ if a human edited)")
    human_edited: bool = Field(default=False, description="True if a human changed the draft before sending")
    edit_explanation: Optional[str] = Field(default=None, description="Filled lazily by the diff-explainer batch job")
    guardrail_passed: Optional[bool] = Field(default=None, description="Result of the safety/guardrail check")
    final_action: Optional[str] = Field(default=None, description="Terminal action, e.g. 'welcome_sent', 'escalated', 'spam_filed'")
    status: str = Field(default="running", description="'running' | 'awaiting_approval' | 'done' | 'error'")
    error: Optional[str] = Field(default=None, description="Error detail if the run failed")


class LeadAgentRunInDB(LeadAgentRun):
    """Database model."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by the database on update")
    model_config = ConfigDict(from_attributes=True)

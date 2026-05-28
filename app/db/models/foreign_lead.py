"""
app/db/models/foreign_lead.py - Read-only models for rows in the landing-pages Supabase project.

These mirror the columns of ``leads`` and ``attorneys`` in the external
landing-pages DB. Cyclone never writes to that DB; these models exist so
the foreign repositories can return typed objects.

Columns we don't use in cyclone (request_headers, ip_address, user_agent
metadata, etc.) are omitted to keep the surface small.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ForeignLead(BaseModel):
    """A row from landing-pages.leads."""
    id: int = Field(..., description="Primary key in the landing-pages DB")
    created_at: datetime = Field(..., description="When the lead was captured")
    updated_at: Optional[datetime] = Field(default=None)
    attorney_id: Optional[int] = Field(default=None, description="FK to landing-pages.attorneys")
    attorney_slug: Optional[str] = Field(default=None, description="Slug copied from the attorney record at capture time")
    url_path: Optional[str] = Field(default=None, description="The page on the landing site the form was submitted from")
    full_name: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    telephone: Optional[str] = Field(default=None)
    audit_score: Optional[int] = Field(default=None, description="Self-assessment score from the landing-page audit tool, 0-100")
    needs_follow_up: Optional[bool] = Field(default=None, description="Legacy follow-up flag from the landing site; cyclone ignores this and uses leads_workflow")
    country: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    zip: Optional[str] = Field(default=None)
    session_uuid: UUID = Field(default_factory=uuid4, description="Stable cross-DB identifier; never null")
    conflict_summary: Optional[str] = Field(default=None, description="Free-form summary of the lead's situation, may be markdown")
    lead_source: Optional[str] = Field(default=None, description="Which landing-page component submitted the lead")
    referrer: Optional[str] = Field(default=None, description="HTTP referrer URL")
    model_config = ConfigDict(from_attributes=True, extra="ignore")


class ForeignAttorney(BaseModel):
    """A row from landing-pages.attorneys. Fields cyclone cares about for the agent."""
    id: int = Field(..., description="Primary key in the landing-pages DB")
    name: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    slug: str = Field(..., description="URL slug; correlates to staff.slug in cyclone")
    contact_email: Optional[str] = Field(default=None)
    phone: Optional[str] = Field(default=None)
    consultation_fee: Optional[str] = Field(default=None, description="Stored as a string in the landing-pages DB, e.g. '$550'")
    firm_name: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    model_config = ConfigDict(from_attributes=True, extra="ignore")

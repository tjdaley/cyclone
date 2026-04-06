"""
app/db/models/matter_event.py - Domain and database models for matter events and deadlines.
"""
from datetime import date, datetime, time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """Category of matter event."""
    hearing = "hearing"
    deposition = "deposition"
    deadline = "deadline"
    mediation = "mediation"
    appointment = "appointment"
    other = "other"


class MatterEvent(BaseModel):
    """
    Domain model for a matter event or deadline.

    Events are read-only from the client portal. Staff create and manage
    them through the admin/matter UI. Future: automated sync from calendar
    integrations (see PRD §11.5).
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    event_type: EventType = Field(..., description="Category of event")
    title: str = Field(..., description="Short title displayed in the calendar and events list")
    description: Optional[str] = Field(
        default=None,
        description="Extended description or notes about the event",
    )
    event_date: date = Field(..., description="Date on which the event occurs")
    event_time: Optional[time] = Field(
        default=None,
        description="Time of day the event begins; null for all-day deadlines",
    )
    location: Optional[str] = Field(
        default=None,
        description="Physical or virtual location (e.g. courtroom number, Zoom link)",
    )
    created_by_staff_id: int = Field(
        ...,
        description="Foreign key to the staff member who created this event",
    )


class MatterEventInDB(MatterEvent):
    """Database model — extends MatterEvent with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

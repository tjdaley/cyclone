"""
app/db/models/attorney_lead_responder.py - M:N between staff (attorney) and staff (responder).

Encodes "which support staff respond to this attorney's PNC leads." Both sides
reference staff: the attorney side must have role='attorney' (enforced in the
app layer, not the FK, since roles can change); the responder side must have
role != 'attorney'. The slug='www' "Firm" staff record holds firm-wide
responders for unattributed / unassigned leads.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttorneyLeadResponder(BaseModel):
    """Domain model for one attorney↔responder mapping."""
    attorney_staff_id: int = Field(..., description="Staff member with role='attorney' whose leads are routed")
    responder_staff_id: int = Field(..., description="Staff member (role != 'attorney') who handles this attorney's PNC leads")


class AttorneyLeadResponderInDB(AttorneyLeadResponder):
    """Database model. Append-only mapping table; no updated_at."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    model_config = ConfigDict(from_attributes=True)

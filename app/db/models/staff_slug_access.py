"""
app/db/models/staff_slug_access.py - Per-staff allowlist of attorney slugs.

Controls which leads (foreign-DB rows) a staff member can see in the CRM,
keyed by the lead's ``attorney_slug``. A slug value of '*' is a wildcard
(see all slugs). Admin role implicitly bypasses this table.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StaffSlugAccess(BaseModel):
    """Domain model for one slug-access grant."""
    staff_id: int = Field(..., description="Staff member granted access")
    slug: str = Field(..., description="Attorney slug this staff member can see leads for; '*' = wildcard")


class StaffSlugAccessInDB(StaffSlugAccess):
    """Database model."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    model_config = ConfigDict(from_attributes=True)

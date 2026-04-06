"""
app/schemas/staff.py - Request and response schemas for staff endpoints.

These are the public API surface. Never return InDB models directly to the
frontend — always go through these schemas.
"""
from typing import List, Optional

from pydantic import BaseModel, Field

from db.models.staff import BarAdmission, FullName, StaffRole


class StaffCreateRequest(BaseModel):
    """Body for POST /api/v1/staff — create a new staff member."""
    supabase_uid: str = Field(..., description="Supabase Auth UID from auth.users")
    role: StaffRole = Field(..., description="Staff role")
    name: FullName = Field(..., description="Full legal name")
    office_id: int = Field(..., description="Foreign key to the offices table")
    email: str = Field(..., description="Work email address")
    telephone: str = Field(..., description="Work telephone number")
    slug: str = Field(..., description="URL-safe unique identifier")
    bar_admissions: List[BarAdmission] = Field(default_factory=list, description="Bar admissions")
    default_billing_rate: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Default hourly billing rate in USD; null for non-billing roles",
    )


class StaffUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/staff/{id} — partial update of a staff member."""
    role: Optional[StaffRole] = None
    name: Optional[FullName] = None
    office_id: Optional[int] = None
    email: Optional[str] = None
    telephone: Optional[str] = None
    slug: Optional[str] = None
    bar_admissions: Optional[List[BarAdmission]] = None
    default_billing_rate: Optional[float] = Field(default=None, ge=0.0)


class StaffResponse(BaseModel):
    """Response body for staff endpoints."""
    id: int
    supabase_uid: str
    role: StaffRole
    name: FullName
    office_id: int
    email: str
    telephone: str
    slug: str
    bar_admissions: List[BarAdmission]
    default_billing_rate: Optional[float]

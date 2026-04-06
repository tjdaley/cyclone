"""
app/db/models/staff.py - Data models for staff members (attorneys, paralegals, admins).

Replaces the illustrative attorney.py starter model.
"""
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class StaffRole(str, Enum):
    """Application roles available to staff members."""
    attorney = "attorney"
    paralegal = "paralegal"
    admin = "admin"


class FullName(BaseModel):
    """Structured representation of a person's full name."""
    courtesy_title: Optional[str] = Field(
        default=None,
        description="Courtesy title, e.g. Mr., Ms., Dr.",
    )
    first_name: str = Field(..., description="First name")
    middle_name: Optional[str] = Field(
        default=None,
        description="Middle name or initial. Follow initial with a period.",
    )
    last_name: str = Field(..., description="Last or family name")
    suffix: Optional[str] = Field(
        default=None,
        description="Generational suffix, e.g. Jr., Sr., III, IV",
    )


class BarAdmission(BaseModel):
    """State bar admission record for an attorney."""
    bar_number: str = Field(..., description="Bar card number issued by the admitting state")
    state: str = Field(..., description="Two-letter state code of admission, e.g. TX")


class StaffMember(BaseModel):
    """
    Domain model for a staff member.

    Covers attorneys, paralegals, and admins. Non-attorneys will have an
    empty ``bar_admissions`` list. ``supabase_uid`` links this record to the
    Supabase Auth user created during onboarding.
    """
    supabase_uid: str = Field(
        ...,
        description="Supabase Auth UID — foreign key to auth.users; set at account creation",
    )
    role: StaffRole = Field(..., description="Staff role governing portal access and permissions")
    name: FullName = Field(..., description="Full legal name of the staff member")
    office_id: int = Field(..., description="Foreign key to the offices table")
    email: str = Field(..., description="Work email address")
    telephone: str = Field(..., description="Work telephone number")
    slug: str = Field(
        ...,
        description="URL-safe unique identifier used in routing and as an alternate lookup key",
    )
    bar_admissions: List[BarAdmission] = Field(  # type: ignore[assignment]
        default_factory=list,
        description="State bar admissions; empty list for non-attorney roles",
    )


class StaffMemberInDB(StaffMember):
    """Database model — extends StaffMember with DB-managed metadata fields."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

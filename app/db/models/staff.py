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

    def __str__(self) -> str:
        """Return the full name as a single string."""
        parts: list[Optional[str]] = [
            self.courtesy_title,
            self.first_name,
            self.middle_name,
            self.last_name,
            self.suffix,
        ]
        return " ".join(part for part in parts if part)  # type: ignore


class BarAdmission(BaseModel):
    """State bar admission record for an attorney."""
    bar_number: str = Field(..., description="Bar card number issued by the admitting state")
    state: str = Field(..., description="Two-letter state code of admission, e.g. TX")


class StaffMember(BaseModel):
    """
    Domain model for a staff member.

    Covers attorneys, paralegals, and admins. Non-attorneys will have an
    empty ``bar_admissions`` list.

    Lifecycle:
    1. Admin creates the record with ``auth_email`` set to the address the
       employee will use to sign in (e.g. their Google account email).
       ``supabase_uid`` is left null at this point.
    2. On the employee's first login the auth correlation endpoint matches
       ``auth_email`` to the Supabase Auth session email and writes the
       resulting ``auth.users.id`` into ``supabase_uid``, forming a permanent
       link between the staff record and the auth system.
    """
    supabase_uid: Optional[str] = Field(
        default=None,
        description="Supabase Auth UID — set automatically on first login via the correlation flow",
    )
    auth_email: Optional[str] = Field(
        default=None,
        description="The email address the staff member will use to sign in (Google / magic link). "
                    "Used once to correlate the Supabase Auth user with this record. "
                    "Distinct from the work email field.",
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
    default_billing_rate: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Default hourly billing rate in USD for this staff member. "
                    "Null for admin roles that do not bill time. "
                    "Overridden per-matter by MatterRateOverride records.",
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

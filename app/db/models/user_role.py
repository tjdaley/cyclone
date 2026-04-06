"""
app/db/models/user_role.py - Domain and database models for the user_roles mapping table.

Maps Supabase Auth UIDs to application roles and optionally to a staff or
client record. This table is the source of truth for role-based access control
in FastAPI; Supabase RLS policies mirror this as a backstop.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserRoleType(str, Enum):
    """All roles that can be assigned to a Supabase Auth user."""
    client = "client"
    attorney = "attorney"
    paralegal = "paralegal"
    admin = "admin"


class UserRole(BaseModel):
    """
    Domain model for a user role assignment.

    Exactly one of ``staff_id`` or ``client_id`` should be non-null,
    depending on whether ``role`` is a staff role or ``client``.
    """
    supabase_uid: str = Field(
        ...,
        description="Supabase Auth UID from auth.users; unique per row",
    )
    role: UserRoleType = Field(..., description="Application role granted to this user")
    staff_id: Optional[int] = Field(
        default=None,
        description="Foreign key to the staff table; populated for attorney, paralegal, admin roles",
    )
    client_id: Optional[int] = Field(
        default=None,
        description="Foreign key to the clients table; populated for the client role",
    )


class UserRoleInDB(UserRole):
    """Database model — extends UserRole with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

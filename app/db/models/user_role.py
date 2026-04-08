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

    This table is the auth entry point. On every authenticated request the
    middleware's UID is looked up here directly:
    ``user_roles WHERE supabase_uid = <jwt sub>``.

    From the resulting record the caller can determine whether this is a
    staff or client user by checking which FK is populated.

    Lifecycle:
    1. Admin creates the record with ``auth_email`` set and
       ``supabase_uid`` null. Exactly one of ``staff_id`` or ``client_id``
       must be populated.
    2. On first login, ``POST /api/v1/auth/correlate-staff`` matches
       ``auth_email`` to the JWT email and writes ``supabase_uid`` into
       both this record and the corresponding ``staff`` record.
    3. Subsequent logins hit ``user_roles WHERE supabase_uid = X`` in a
       single query.
    """
    supabase_uid: Optional[str] = Field(
        default=None,
        description="Supabase Auth UID — null until first-login correlation",
    )
    auth_email: Optional[str] = Field(
        default=None,
        description="Email used for first-login correlation; matches the Google/magic-link address",
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

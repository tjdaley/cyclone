"""
app/db/models/user_role.py - Domain and database models for the user_roles mapping table.

Maps Supabase Auth UIDs to application roles and optionally to a staff or
client record. This table is the source of truth for role-based access control
in FastAPI; Supabase RLS policies mirror this as a backstop.

A single user may have MULTIPLE rows here — one per role they hold (e.g. an
attorney who is also an admin has two rows with the same ``supabase_uid``).
RBAC checks intersect the user's role set with the allowed roles; the
``primary_role()`` helper picks the highest-privilege role for display and for
legacy single-role consumers.
"""
from datetime import datetime
from enum import Enum
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserRoleType(str, Enum):
    """All roles that can be assigned to a Supabase Auth user."""
    client = "client"
    attorney = "attorney"
    paralegal = "paralegal"
    admin = "admin"


# Highest-privilege first. ``primary_role()`` returns the first match.
_ROLE_PRIORITY: list[UserRoleType] = [
    UserRoleType.admin,
    UserRoleType.attorney,
    UserRoleType.paralegal,
    UserRoleType.client,
]


def primary_role(role_values: Iterable[str]) -> Optional[str]:
    """Return the highest-privilege role from a set of role values, or None
    if the set is empty. Used wherever a single 'effective role' is needed
    (UI display, density selection, the legacy ``role`` field in /me)."""
    values = set(role_values)
    for r in _ROLE_PRIORITY:
        if r.value in values:
            return r.value
    return None


class UserRole(BaseModel):
    """
    Domain model for ONE role assignment.

    A single user may have multiple rows here. The auth entry point is:
    ``user_roles WHERE supabase_uid = <jwt sub>`` — which can return one OR
    multiple rows. Callers must use ``select_many`` (not ``select_one``,
    which fails with PGRST116 on multiple matches).

    From the resulting set the caller determines (a) the user's role set
    for RBAC, (b) the primary role for display via ``primary_role(...)``,
    and (c) the linked ``staff_id`` / ``client_id`` (all rows for the same
    user should agree on these).

    Lifecycle:
    1. Admin creates one or more rows with ``auth_email`` set and
       ``supabase_uid`` null. Exactly one of ``staff_id`` or ``client_id``
       must be populated per row.
    2. On first login, ``POST /api/v1/auth/correlate-staff`` matches
       ``auth_email`` to the JWT email and writes ``supabase_uid`` into
       every unlinked row (and into the matching ``staff`` record once).
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

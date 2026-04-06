"""
app/db/models/client.py - Domain and database models for clients.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from db.models.staff import FullName


class ClientStatus(str, Enum):
    """Lifecycle status of a client record."""
    prospect = "prospect"
    pending_conflict_check = "pending_conflict_check"
    conflict_flagged = "conflict_flagged"
    active = "active"
    inactive = "inactive"


class Client(BaseModel):
    """
    Domain model for a client.

    A client may be linked to one or more matters. Contact and status
    information is stored here; financial balances are derived from the
    trust_ledger and billing_entries tables at runtime.
    """
    name: FullName = Field(..., description="Client's full legal name")
    email: str = Field(..., description="Primary email address for billing statements and portal access")
    telephone: str = Field(..., description="Primary telephone number")
    referral_source: Optional[str] = Field(
        default=None,
        description="How the client was referred to the firm, e.g. 'Google', 'Existing Client'",
    )
    status: ClientStatus = Field(
        default=ClientStatus.prospect,
        description="Lifecycle status of the client record",
    )
    prior_counsel: Optional[str] = Field(
        default=None,
        description="Name of prior counsel, if any, for conflict-check purposes",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Internal intake notes; not visible to the client",
    )


class ClientInDB(Client):
    """Database model — extends Client with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

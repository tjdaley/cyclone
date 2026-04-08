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
    auth_email: str = Field(
        ...,
        description="Email address the client will use to sign in to the client portal. "
                    "Used for first-login correlation, same as staff.auth_email.",
    )
    email: str = Field(..., description="Primary email address for billing statements and correspondence")
    telephone: str = Field(..., description="Primary telephone number")
    referral_type: str = Field(
        ...,
        description="Category of referral: 'attorney', 'former client', 'search', 'ai', 'other'. "
                    "Valid values come from settings.referral_types.",
    )
    referral_source: str = Field(
        ...,
        description="Name of the referring attorney, former client, search engine, AI vendor, etc.",
    )
    referred_to_staff_id: Optional[int] = Field(
        default=None,
        description="FK to staff table — who the matter was referred to. Null means referred to firm generally.",
    )
    status: ClientStatus = Field(
        default=ClientStatus.prospect,
        description="Lifecycle status of the client record",
    )
    prior_counsel: Optional[str] = Field(
        default=None,
        description="Name of prior counsel, if any, for conflict-check purposes",
    )
    ok_to_rehire: bool = Field(
        default=True,
        description="Would the firm take another matter from this client?",
    )
    ending_ar_balance: float = Field(
        default=0.0,
        description="Ending accounts receivable balance in USD",
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

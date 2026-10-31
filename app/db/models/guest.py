"""
app/db/models/guest.py - Financial professional guests and their access to matters (052).

A guest is a tracing expert, CPA or similar given read access to one or more
matters' Accounts, Transactions and Inventory, with the client's consent.
Design: docs/specs/financial-guest-access.md.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GuestDesignation(str, Enum):
    """What the guest is to the case. Decides what is disclosable."""
    testifying_expert = "testifying_expert"
    consulting_expert = "consulting_expert"
    non_expert = "non_expert"


class GrantStatus(str, Enum):
    """
    The stored state of a grant. ``expired`` is NOT stored: it is an active grant
    whose ``expires_at`` has passed, decided at request time, so a job that
    failed to run can never leave access open.
    """
    pending_consent = "pending_consent"
    active = "active"
    revoked = "revoked"


class ConsentMethod(str, Enum):
    """How the client gave the consent a staff member is attesting to."""
    phone = "phone"
    in_person = "in_person"
    email = "email"
    letter = "letter"
    other = "other"


class GuestProfessional(BaseModel):
    """The person."""
    firm_id: int = Field(default=1, description="FK to firms. Attribution cannot be backfilled")
    full_name: str = Field(..., description="Name as it should read on a record")
    email: str = Field(..., description="Sign-in address; correlates the user_roles row")
    organization: Optional[str] = Field(default=None, description="Their firm or practice")
    profession: Optional[str] = Field(default=None, description="e.g. 'Forensic accountant'")
    is_active: bool = Field(default=True, description="False blocks every grant at once")
    created_by_staff_id: Optional[int] = Field(default=None, description="Who added them")


class GuestProfessionalInDB(GuestProfessional):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class MatterGuestGrant(BaseModel):
    """One guest's access to one matter."""
    matter_id: int = Field(..., description="FK to matters")
    guest_id: int = Field(..., description="FK to guest_professionals")
    designation: GuestDesignation = Field(..., description="Testifying, consulting, or neither")
    purpose: Optional[str] = Field(default=None, description="What they were retained to do")
    status: GrantStatus = Field(
        default=GrantStatus.pending_consent,
        description="Only ever active on a recorded consent")
    expires_at: datetime = Field(..., description="Required. Past it, the grant opens nothing")
    requested_by_staff_id: Optional[int] = Field(default=None, description="Who set it up")
    requested_by: Optional[str] = Field(default=None, description="Their name")
    activated_at: Optional[datetime] = Field(default=None, description="When consent was recorded")
    revoked_at: Optional[datetime] = Field(default=None, description="When access was withdrawn")
    revoked_by_staff_id: Optional[int] = Field(default=None, description="Who withdrew it")
    revoked_by: Optional[str] = Field(default=None, description="Their name")
    revoke_reason: Optional[str] = Field(default=None, description="Why, if anybody said")


class MatterGuestGrantInDB(MatterGuestGrant):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class GuestConsent(BaseModel):
    """A staff member's attestation that the client consented to one grant."""
    grant_id: int = Field(..., description="FK to matter_guest_grants")
    method: ConsentMethod = Field(..., description="How the client gave it")
    consent_date: date = Field(..., description="The day the client gave it")
    note: Optional[str] = Field(default=None, description="Anything worth recording")
    attested_by_staff_id: int = Field(..., description="Who is attesting")
    attested_by: str = Field(..., description="Their name, as the record reads")


class GuestConsentInDB(GuestConsent):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class GuestDocumentLedgerEntry(BaseModel):
    """
    One statement made available to a guest under one grant.

    ``statement_id`` is deliberately not a foreign key: a rejected statement is
    hard-deleted, and the record of what an expert was provided must outlive it.
    """
    grant_id: int = Field(..., description="FK to matter_guest_grants")
    statement_id: int = Field(..., description="The statement, which may since have been deleted")
    financial_account_id: Optional[int] = Field(default=None, description="Its account")
    account_label: Optional[str] = Field(default=None, description="Institution and last four")
    period_start: Optional[date] = Field(default=None, description="Statement period start")
    period_end: Optional[date] = Field(default=None, description="Statement period end")
    source_filename: Optional[str] = Field(default=None, description="Filename as produced")
    bates_first: Optional[str] = Field(default=None, description="First Bates stamp")
    bates_last: Optional[str] = Field(default=None, description="Last Bates stamp")
    first_available_at: Optional[datetime] = Field(default=None, description="First seen available")
    removed_at: Optional[datetime] = Field(default=None, description="When it stopped existing")


class GuestDocumentLedgerEntryInDB(GuestDocumentLedgerEntry):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class GuestSignIn(BaseModel):
    """A guest signed in. For revocation audits, not for disclosure."""
    guest_id: int = Field(..., description="FK to guest_professionals")
    signed_in_at: Optional[datetime] = Field(default=None, description="When")


class GuestSignInInDB(GuestSignIn):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)

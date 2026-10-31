"""
app/schemas/guest.py - Request and response shapes for financial-professional access (052).

The guest-facing shapes are written for the guest, not cut down from the staff
ones: a field added to a staff schema must never reach an expert by default.
That is why there is no ``**model_dump()`` into a staff response anywhere here.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from db.models.financial import AccountOwnership, AccountType, PropertyCharacter
from db.models.guest import ConsentMethod, GuestDesignation


# ── Staff side ───────────────────────────────────────────────────────────────

class GuestInviteRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)
    organization: Optional[str] = Field(default=None, max_length=200)
    profession: Optional[str] = Field(default=None, max_length=200)
    designation: GuestDesignation
    purpose: Optional[str] = Field(default=None, max_length=1000)
    expires_in_days: int = Field(default=180, ge=1, le=730)


class GuestConsentRequest(BaseModel):
    """A staff member attesting that the client consented."""
    method: ConsentMethod
    consent_date: date
    note: Optional[str] = Field(default=None, max_length=2000)


class GuestRevokeRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)
    everywhere: bool = Field(
        default=False, description="Withdraw this guest's access on every matter, not just this one")


class GuestRenewRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=730)


class GuestDesignationRequest(BaseModel):
    designation: GuestDesignation


class GuestConsentResponse(BaseModel):
    method: ConsentMethod
    consent_date: date
    note: Optional[str] = None
    attested_by: str
    created_at: datetime


class GuestGrantResponse(BaseModel):
    """One guest's access to this matter, as staff see it."""
    id: int
    guest_id: int
    full_name: str
    email: str
    organization: Optional[str] = None
    profession: Optional[str] = None
    designation: GuestDesignation
    purpose: Optional[str] = None
    status: str = Field(..., description="pending_consent, active, expired or revoked — as of now")
    expires_at: datetime
    requested_by: Optional[str] = None
    activated_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None
    consents: list[GuestConsentResponse] = Field(default_factory=list)
    documents_provided: int = 0
    last_signed_in_at: Optional[datetime] = None
    created_at: datetime


class GuestLedgerEntryResponse(BaseModel):
    statement_id: int
    account_label: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    source_filename: Optional[str] = None
    bates_first: Optional[str] = None
    bates_last: Optional[str] = None
    first_available_at: datetime
    removed_at: Optional[datetime] = None


# ── Guest side ───────────────────────────────────────────────────────────────

class GuestMatter(BaseModel):
    id: int
    name: str
    designation: GuestDesignation
    expires_at: datetime
    purpose_reminder: str


class GuestAccount(BaseModel):
    """An account on the matter. No notes, no purpose: those are attorney work product."""
    id: int
    institution: str
    account_type: AccountType
    account_number_last4: Optional[str] = None
    name_on_account: Optional[str] = None
    ownership: AccountOwnership
    property_character: Optional[PropertyCharacter] = None
    is_closed: bool = False
    currency: str = "USD"


class GuestStatement(BaseModel):
    """A statement. No flags, no provenance, no review status."""
    id: int
    financial_account_id: int
    period_start: date
    period_end: date
    beginning_balance: Optional[Decimal] = None
    ending_balance: Optional[Decimal] = None
    reconciled: bool = False
    bates_first: Optional[str] = None
    bates_last: Optional[str] = None
    source_filename: Optional[str] = None
    has_pdf: bool = False


class GuestTransaction(BaseModel):
    id: int
    statement_id: int
    financial_account_id: int
    transaction_date: Optional[date] = None
    posted_date: Optional[date] = None
    description: str
    counterparty: Optional[str] = None
    location: Optional[str] = None
    amount: Decimal
    running_balance: Optional[Decimal] = None
    check_number: Optional[str] = None
    category_id: Optional[int] = Field(default=None, description="Shown, never set by a guest")
    physical_page_number: Optional[int] = None
    bates_number: Optional[str] = None
    institution: Optional[str] = None
    account_last4: Optional[str] = None
    currency: str = "USD"
    tag_ids: list[int] = Field(default_factory=list, description="This guest's own tags only")


class GuestTransactionPage(BaseModel):
    total: int
    items: list[GuestTransaction]
    sum_amount: str


class GuestTag(BaseModel):
    id: int
    label: str
    description: Optional[str] = None
    color: Optional[str] = None
    is_active: bool = True


class GuestTagWriteRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)
    color: Optional[str] = Field(default=None, max_length=40)
    is_active: Optional[bool] = None


class GuestTagApplyRequest(BaseModel):
    transaction_ids: list[int] = Field(..., min_length=1, max_length=5000)
    tag_id: int
    remove: bool = False


class GuestCategory(BaseModel):
    """A category's name, so a guest can read and filter on what staff filed."""
    id: int
    description: str
    parent_id: Optional[int] = None

"""
app/schemas/billing.py - Request and response schemas for billing endpoints.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from db.models.billing_entry import EntryType
from db.models.billing_cycle import BillingCycleStatus


# ── Billing Entries ────────────────────────────────────────────────────────

class BillingEntryCreateRequest(BaseModel):
    """Body for POST /api/v1/billing/entries.

    ``staff_id`` is optional — if omitted the server resolves it from the
    authenticated user's JWT. ``entry_date`` is always set server-side to
    today. ``invoice_date`` is the date the work was performed; defaults to
    today if not provided. It can be set explicitly via a date picker or
    parsed from the natural-language input.
    """
    matter_id: int
    staff_id: Optional[int] = Field(default=None, description="Timekeeper; resolved from JWT if omitted")
    entry_type: EntryType
    invoice_date: Optional[date] = Field(default=None, description="Date work was performed; defaults to today")
    hours: Optional[float] = Field(default=None, ge=0.0)
    rate: Optional[float] = Field(default=None, ge=0.0)
    amount: Optional[float] = Field(default=None, ge=0.0)
    description: str
    billable: bool = True


class BillingEntryUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/billing/entries/{id}."""
    invoice_date: Optional[date] = None
    hours: Optional[float] = Field(default=None, ge=0.0)
    rate: Optional[float] = Field(default=None, ge=0.0)
    amount: Optional[float] = Field(default=None, ge=0.0)
    description: Optional[str] = None
    billable: Optional[bool] = None


class BillingEntryResponse(BaseModel):
    """Response for a single billing entry."""
    id: int
    matter_id: int
    staff_id: int
    billing_cycle_id: Optional[int]
    entry_type: EntryType
    entry_date: date
    invoice_date: date
    hours: Optional[float]
    rate: Optional[float]
    amount: Optional[float]
    description: str
    billable: bool
    billed: bool


# ── Natural Language Parsing ───────────────────────────────────────────────

class NLBillingParseRequest(BaseModel):
    """Body for POST /api/v1/billing/parse."""
    text: str = Field(..., description="Natural language billing description")
    matter_id: Optional[int] = Field(default=None, description="Matter to resolve rate for; if omitted, rate/amount will be null")


class NLBillingParseResponse(BaseModel):
    """
    Response from the natural language billing parse endpoint.

    This is a preview — not committed until the attorney POSTs to
    /api/v1/billing/entries.
    """
    entry_type: str = Field(..., description="'time' | 'expense' | 'flat_fee'")
    description: str = Field(..., description="Clean, professional billing description")
    hours: Optional[float] = Field(default=None, description="Hours billed (time entries only)")
    rate: Optional[float] = Field(default=None, description="Resolved hourly rate in USD")
    amount: Optional[float] = Field(default=None, description="Computed amount (hours * rate)")
    invoice_date: Optional[str] = Field(default=None, description="Parsed service date (ISO format) or null if not mentioned")
    billable: bool = Field(default=True, description="Whether this entry is billable")
    confidence: str = Field(..., description="'high' | 'medium' | 'low'")


# ── Billing Cycles ─────────────────────────────────────────────────────────

class BillingCycleCreateRequest(BaseModel):
    """Body for POST /api/v1/billing/cycles."""
    matter_id: int
    period_start: date
    period_end: date


class BillingCycleResponse(BaseModel):
    """Response for a billing cycle."""
    id: int
    matter_id: int
    period_start: date
    period_end: date
    status: BillingCycleStatus
    closed_by_staff_id: Optional[int]
    bill_storage_path: Optional[str]
    stripe_payment_link: Optional[str]


class CloseCycleRequest(BaseModel):
    """Body for POST /api/v1/billing/cycles/{id}/close."""
    staff_id: int = Field(..., description="Staff member closing the cycle")


# ── Client Balance ─────────────────────────────────────────────────────────

class ClientBalanceResponse(BaseModel):
    """Response for GET /api/v1/billing/balance/{matter_id}."""
    matter_id: int
    trust_balance: float
    unbilled_total: float
    balance: float
    status: str = Field(..., description="'green' | 'yellow' | 'red'")

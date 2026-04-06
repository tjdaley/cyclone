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
    """Body for POST /api/v1/billing/entries."""
    matter_id: int
    staff_id: int
    entry_type: EntryType
    entry_date: date
    hours: Optional[float] = Field(default=None, ge=0.0)
    rate: Optional[float] = Field(default=None, ge=0.0)
    amount: Optional[float] = Field(default=None, ge=0.0)
    description: str
    billable: bool = True


class BillingEntryUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/billing/entries/{id}."""
    entry_date: Optional[date] = None
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


class NLBillingParseResponse(BaseModel):
    """
    Response from the natural language billing parse endpoint.

    The ``parsed`` object is a preview only — not committed until the
    attorney clicks Commit (POST /api/v1/billing/entries).
    """
    parsed: dict = Field(..., description="Structured fields extracted from the input text")
    confidence: str = Field(..., description="'high' | 'medium' | 'low' — LLM self-assessment")


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

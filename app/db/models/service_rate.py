"""
app/db/models/service_rate.py - What a vendor charges, and from when.

Migration 041. Prices are applied to the ``llm_calls`` ledger when a report
runs, never stored on it -- see ``docs/cost-tracking-design.md``.
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RateMeter(str, Enum):
    """Which usage a price applies to. Values must match the service_rates CHECK."""

    input_uncached = "input_uncached"
    input_cache_read = "input_cache_read"
    input_cache_write = "input_cache_write"
    output = "output"


class ServiceRate(BaseModel):
    """One price for one meter of one model, in force for a half-open period."""

    provider: str = Field(..., description="llm_calls.vendor this applies to, e.g. 'anthropic'")
    model: str = Field(..., description="llm_calls.model this applies to, exactly as recorded")
    meter: RateMeter = Field(..., description="Which normalized usage column this prices")
    unit_count: int = Field(default=1_000_000, description="Price is per this many units")
    price: Decimal = Field(..., description="Price per unit_count, in currency. Never a float")
    currency: str = Field(default="USD", description="Always USD until a migration says otherwise")
    effective_from: datetime = Field(..., description="Inclusive start of the period")
    effective_to: Optional[datetime] = Field(
        default=None, description="Exclusive end; None while the price is still in force")
    source_url: Optional[str] = Field(
        default=None, description="Vendor pricing page the figure was copied from")
    note: Optional[str] = Field(default=None, description="Why this price, in a sentence")
    created_by_staff_id: Optional[int] = Field(
        default=None, description="Staff member who entered it. Not a FK")


class ServiceRateInDB(ServiceRate):
    """Database model."""

    id: int = Field(..., description="Primary key, set by database")
    created_at: datetime = Field(..., description="Set by database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by database on update")
    model_config = ConfigDict(from_attributes=True)

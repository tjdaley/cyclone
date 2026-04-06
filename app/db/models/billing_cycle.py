"""
app/db/models/billing_cycle.py - Domain and database models for billing cycles.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BillingCycleStatus(str, Enum):
    """Lifecycle status of a billing cycle."""
    open = "open"
    closed = "closed"


class BillingCycle(BaseModel):
    """
    Domain model for a billing cycle.

    A billing cycle groups billing entries for a matter over a date range.
    Once ``status`` transitions to ``closed``, the cycle is immutable and
    the associated bill is generated. An audit log entry is written at close.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    period_start: date = Field(..., description="First day of the billing period (inclusive)")
    period_end: date = Field(..., description="Last day of the billing period (inclusive)")
    status: BillingCycleStatus = Field(
        default=BillingCycleStatus.open,
        description="open = entries may be added; closed = bill generated and immutable",
    )
    closed_by_staff_id: Optional[int] = Field(
        default=None,
        description="Foreign key to the staff member who closed this cycle",
    )
    bill_storage_path: Optional[str] = Field(
        default=None,
        description="Supabase Storage path to the generated PDF bill",
    )
    stripe_payment_link: Optional[str] = Field(
        default=None,
        description="Stripe-hosted payment URL appended to the bill",
    )

    @model_validator(mode="after")
    def validate_period(self) -> "BillingCycle":
        """Ensure period_end is not before period_start."""
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


class BillingCycleInDB(BillingCycle):
    """Database model — extends BillingCycle with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

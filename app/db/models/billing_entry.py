"""
app/db/models/billing_entry.py - Domain and database models for billing entries.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntryType(str, Enum):
    """Category of billing entry."""
    time = "time"
    expense = "expense"
    flat_fee = "flat_fee"


class BillingEntry(BaseModel):
    """
    Domain model for a single billing entry.

    - ``time`` entries carry ``hours`` and ``rate``; ``amount`` is computed
      as ``hours * rate`` unless ``amount`` is explicitly set (override).
    - ``expense`` entries carry ``amount`` only; ``hours`` and ``rate`` are null.
    - ``flat_fee`` entries carry ``amount`` only; ``hours`` is null.

    The ``billable`` flag controls whether the entry appears on a client bill.
    ``billed`` is set to True when the entry is closed into a billing cycle.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    staff_id: int = Field(..., description="Foreign key to the timekeeper (staff) on this entry")
    billing_cycle_id: Optional[int] = Field(
        default=None,
        description="Foreign key to the billing_cycles table; null until the entry is included in a cycle",
    )
    entry_type: EntryType = Field(..., description="Category of entry: time, expense, or flat_fee")
    entry_date: date = Field(..., description="Date the billing entry was recorded (server-set)")
    invoice_date: date = Field(..., description="Date the work was performed or the expense was incurred")
    hours: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Duration in hours (time entries only); must match a configured time increment",
    )
    rate: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Hourly rate in USD (time entries only); defaults to matter rate card if not overridden",
    )
    amount: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Billed amount in USD. For time entries this overrides hours*rate. Required for expense/flat_fee",
    )
    description: str = Field(..., description="Billing narrative; appears on client statements")
    billable: bool = Field(default=True, description="Whether this entry appears on a client bill")
    billed: bool = Field(default=False, description="True once the entry has been included in a closed billing cycle")
    receipt_storage_path: Optional[str] = Field(
        default=None,
        description="Supabase Storage path to an uploaded receipt (expense entries only)",
    )

    @model_validator(mode="after")
    def validate_entry_fields(self) -> "BillingEntry":
        """Enforce field constraints by entry type."""
        if self.entry_type == EntryType.time:
            if self.hours is None:
                raise ValueError("hours is required for time entries")
            if self.rate is None and self.amount is None:
                raise ValueError("rate or amount must be provided for time entries")
        if self.entry_type in (EntryType.expense, EntryType.flat_fee):
            if self.amount is None:
                raise ValueError("amount is required for expense and flat_fee entries")
        return self


class BillingEntryInDB(BillingEntry):
    """Database model — extends BillingEntry with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

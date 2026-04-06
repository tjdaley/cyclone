"""
app/db/models/trust_ledger.py - Domain and database models for trust account ledger entries.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TrustTransactionType(str, Enum):
    """Direction of a trust ledger transaction."""
    deposit = "deposit"       # Funds received from client
    withdrawal = "withdrawal"  # Funds disbursed (e.g. applied to bill)
    refund = "refund"         # Funds returned to client


class TrustLedgerEntry(BaseModel):
    """
    Domain model for a single trust account transaction.

    Trust ledger entries are append-only; never updated or deleted.
    An audit log entry is written for every posted transaction.

    The running balance is derived by summing all entries for a matter at
    query time: ``SUM(amount) WHERE matter_id = ?`` (deposits positive,
    withdrawals/refunds negative).
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    transaction_type: TrustTransactionType = Field(
        ...,
        description="Direction of the transaction: deposit, withdrawal, or refund",
    )
    amount: float = Field(
        ...,
        gt=0.0,
        description="Absolute transaction amount in USD (always positive; direction set by transaction_type)",
    )
    transaction_date: date = Field(..., description="Date the transaction was posted")
    description: str = Field(..., description="Memo describing the purpose of the transaction")
    posted_by_staff_id: int = Field(
        ...,
        description="Foreign key to the staff member who posted this transaction",
    )
    reference_number: Optional[str] = Field(
        default=None,
        description="Check number, wire reference, or other external identifier",
    )


class TrustLedgerEntryInDB(TrustLedgerEntry):
    """Database model — extends TrustLedgerEntry with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    # Trust entries are immutable — no updated_at
    model_config = ConfigDict(from_attributes=True)

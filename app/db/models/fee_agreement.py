"""
app/db/models/fee_agreement.py - Domain and database models for fee agreements.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class FeeAgreementStatus(str, Enum):
    """Lifecycle status of a fee agreement."""
    draft = "draft"
    sent_to_client = "sent_to_client"
    executed = "executed"
    voided = "voided"


class FeeAgreement(BaseModel):
    """
    Domain model for a fee agreement.

    Phase 1: checkbox acknowledgment + timestamp stored in ``signed_at``.
    Phase 2 (future): DocuSign/HelloSign envelope ID stored in
    ``external_signature_id``; signed PDF stored in Supabase Storage.

    An audit log entry is written when ``status`` transitions to ``executed``.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    template_id: Optional[int] = Field(
        default=None,
        description="Foreign key to the fee agreement template used to generate this agreement",
    )
    status: FeeAgreementStatus = Field(
        default=FeeAgreementStatus.draft,
        description="Lifecycle status of the agreement",
    )
    retainer_amount: float = Field(
        ...,
        ge=0.0,
        description="Retainer amount specified in this agreement, in USD",
    )
    refresh_trigger_pct: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Retainer replenishment threshold fraction (e.g. 0.40 = 40%)",
    )
    signed_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the client acknowledged/signed the agreement",
    )
    signed_by_supabase_uid: Optional[str] = Field(
        default=None,
        description="Supabase Auth UID of the client who signed",
    )
    storage_path: Optional[str] = Field(
        default=None,
        description="Supabase Storage path to the executed PDF snapshot",
    )
    external_signature_id: Optional[str] = Field(
        default=None,
        description="DocuSign/HelloSign envelope ID (Phase 2 e-signature)",
    )


class FeeAgreementInDB(FeeAgreement):
    """Database model — extends FeeAgreement with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

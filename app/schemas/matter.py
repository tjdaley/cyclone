"""
app/schemas/matter.py - Request and response schemas for matter endpoints.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from db.models.matter import DiscoveryLevel, MatterStatus, MatterType, RateCard


class MatterCreateRequest(BaseModel):
    """Body for POST /api/v1/matters."""
    client_id: int = Field(..., description="Primary client for this matter")
    short_name: Optional[str] = Field(default=None, description="Short display name, e.g. 'SALMONS - divorce - 2026'")
    matter_name: str = Field(..., description="Human-readable matter name")
    matter_type: MatterType = Field(..., description="Category of matter")
    billing_review_staff_id: Optional[int] = Field(default=None)
    rate_card: RateCard = Field(default_factory=RateCard, description="Per-role hourly rates")
    retainer_amount: float = Field(default=0.0, ge=0.0)
    refresh_trigger_pct: float = Field(default=0.40, ge=0.0, le=1.0)
    is_pro_bono: bool = Field(default=False)
    fee_agreement_signed_date: Optional[date] = Field(default=None)
    opened_date: Optional[date] = Field(default=None)
    closed_date: Optional[date] = Field(default=None)
    state: str = Field(default="Texas")
    county: str = Field(..., description="County where the matter is filed")
    court_name: Optional[str] = Field(default=None)
    matter_number: Optional[str] = Field(default=None)
    discovery_level: Optional[DiscoveryLevel] = Field(default=None)
    notes: Optional[str] = None


class MatterUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/matters/{id}."""
    short_name: Optional[str] = None
    matter_name: Optional[str] = None
    status: Optional[MatterStatus] = None
    billing_review_staff_id: Optional[int] = None
    rate_card: Optional[RateCard] = None
    retainer_amount: Optional[float] = Field(default=None, ge=0.0)
    refresh_trigger_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_pro_bono: Optional[bool] = None
    fee_agreement_signed_date: Optional[date] = None
    opened_date: Optional[date] = None
    closed_date: Optional[date] = None
    state: Optional[str] = None
    county: Optional[str] = None
    court_name: Optional[str] = None
    matter_number: Optional[str] = None
    discovery_level: Optional[DiscoveryLevel] = None
    notes: Optional[str] = None


class MatterResponse(BaseModel):
    """Response body for matter endpoints."""
    id: int
    client_id: int
    short_name: Optional[str]
    matter_name: str
    matter_type: MatterType
    status: MatterStatus
    billing_review_staff_id: Optional[int]
    rate_card: RateCard
    retainer_amount: float
    refresh_trigger_pct: float
    is_pro_bono: bool
    fee_agreement_signed_date: Optional[date]
    opened_date: Optional[date]
    closed_date: Optional[date]
    state: str
    county: str
    court_name: Optional[str]
    matter_number: Optional[str]
    discovery_level: Optional[DiscoveryLevel]
    notes: Optional[str]


class MatterRateOverrideRequest(BaseModel):
    """Body for POST /api/v1/matters/{id}/rate-overrides."""
    staff_id: int = Field(..., description="Staff member whose rate is overridden")
    rate: float = Field(..., ge=0.0, description="Override hourly rate in USD")


class MatterRateOverrideResponse(BaseModel):
    """Response for a rate override record."""
    id: int
    matter_id: int
    staff_id: int
    rate: float


class MatterStaffRequest(BaseModel):
    """Body for POST /api/v1/matters/{id}/staff."""
    staff_id: int
    role: str = Field(..., description="'originating' | 'billing_reviewer' | 'assigned'")
    split_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)


class OpposingPartyRequest(BaseModel):
    """Body for POST /api/v1/matters/{id}/opposing-parties."""
    full_name: str = Field(..., description="Full name of the opposing party")
    relationship: Optional[str] = None


class OpposingPartyResponse(BaseModel):
    """Response for an opposing party record."""
    id: int
    matter_id: int
    full_name: str
    relationship: Optional[str]

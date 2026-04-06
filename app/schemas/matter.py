"""
app/schemas/matter.py - Request and response schemas for matter endpoints.
"""
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from db.models.matter import MatterStatus, MatterType


class MatterCreateRequest(BaseModel):
    """Body for POST /api/v1/matters."""
    client_id: int = Field(..., description="Primary client for this matter")
    matter_name: str = Field(..., description="Human-readable matter name")
    matter_type: MatterType = Field(..., description="Category of matter")
    billing_review_staff_id: Optional[int] = Field(default=None)
    rate_card: dict[str, Any] = Field(default_factory=dict, description="Rate card by role or staff_id")
    retainer_amount: float = Field(default=0.0, ge=0.0)
    refresh_trigger_pct: float = Field(default=0.40, ge=0.0, le=1.0)
    is_pro_bono: bool = Field(default=False)
    notes: Optional[str] = None


class MatterUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/matters/{id}."""
    matter_name: Optional[str] = None
    status: Optional[MatterStatus] = None
    billing_review_staff_id: Optional[int] = None
    rate_card: Optional[dict[str, Any]] = None
    retainer_amount: Optional[float] = Field(default=None, ge=0.0)
    refresh_trigger_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    is_pro_bono: Optional[bool] = None
    notes: Optional[str] = None


class MatterResponse(BaseModel):
    """Response body for matter endpoints."""
    id: int
    client_id: int
    matter_name: str
    matter_type: MatterType
    status: MatterStatus
    billing_review_staff_id: Optional[int]
    rate_card: dict[str, Any]
    retainer_amount: float
    refresh_trigger_pct: float
    is_pro_bono: bool
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

"""
app/schemas/client.py - Request and response schemas for client endpoints.
"""
from typing import Optional

from pydantic import BaseModel, Field

from db.models.client import ClientStatus
from db.models.staff import FullName


class ClientCreateRequest(BaseModel):
    """Body for POST /api/v1/clients — create a new client record."""
    name: FullName = Field(..., description="Client's full legal name")
    auth_email: str = Field(..., description="Email for client portal login (used in correlation flow)")
    email: str = Field(..., description="Primary email address for billing/correspondence")
    telephone: str = Field(..., description="Primary telephone number")
    referral_type: str = Field(..., description="Category of referral (from settings.referral_types)")
    referral_source: str = Field(..., description="Name of referring attorney, client, search engine, etc.")
    referred_to_staff_id: Optional[int] = Field(default=None, description="Staff member the matter was referred to")
    prior_counsel: Optional[str] = Field(default=None, description="Prior counsel name")
    notes: Optional[str] = Field(default=None, description="Intake notes")


class ClientUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/clients/{id}."""
    name: Optional[FullName] = None
    auth_email: Optional[str] = None
    email: Optional[str] = None
    telephone: Optional[str] = None
    referral_type: Optional[str] = None
    referral_source: Optional[str] = None
    referred_to_staff_id: Optional[int] = None
    prior_counsel: Optional[str] = None
    status: Optional[ClientStatus] = None
    ok_to_rehire: Optional[bool] = None
    ending_ar_balance: Optional[float] = None
    notes: Optional[str] = None


class ClientResponse(BaseModel):
    """Response body for client endpoints."""
    id: int
    name: FullName
    auth_email: str
    email: str
    telephone: str
    referral_type: str
    referral_source: str
    referred_to_staff_id: Optional[int]
    prior_counsel: Optional[str]
    status: ClientStatus
    ok_to_rehire: bool
    ending_ar_balance: float
    notes: Optional[str]


class ConflictCheckRequest(BaseModel):
    """Body for POST /api/v1/clients/conflict-check."""
    full_name: str = Field(..., description="Full name of the prospective client")
    opposing_names: list[str] = Field(
        default_factory=list,
        description="Names of known opposing parties on the prospective matter",
    )


class ConflictCheckResponse(BaseModel):
    """Response from the conflict check endpoint."""
    has_conflict: bool = Field(..., description="True if any potential conflicts were found")
    hit_count: int = Field(..., description="Number of potential conflict hits")
    hits: list[dict] = Field(..., description="List of conflict hit details (entity_id references only)")

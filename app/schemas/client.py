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
    email: str = Field(..., description="Primary email address")
    telephone: str = Field(..., description="Primary telephone number")
    referral_source: Optional[str] = Field(default=None, description="Referral source")
    prior_counsel: Optional[str] = Field(default=None, description="Prior counsel name")
    notes: Optional[str] = Field(default=None, description="Intake notes")


class ClientUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/clients/{id}."""
    name: Optional[FullName] = None
    email: Optional[str] = None
    telephone: Optional[str] = None
    referral_source: Optional[str] = None
    prior_counsel: Optional[str] = None
    status: Optional[ClientStatus] = None
    notes: Optional[str] = None


class ClientResponse(BaseModel):
    """Response body for client endpoints."""
    id: int
    name: FullName
    email: str
    telephone: str
    referral_source: Optional[str]
    prior_counsel: Optional[str]
    status: ClientStatus
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

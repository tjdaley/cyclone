"""
app/schemas/discovery.py - Request and response schemas for discovery endpoints.
"""
from typing import Optional

from pydantic import BaseModel, Field

from db.models.discovery import DiscoveryRequestStatus, DiscoveryRequestType, RFASelection


class DiscoveryIngestRequest(BaseModel):
    """Body for POST /api/v1/discovery/ingest."""
    matter_id: int = Field(..., description="Matter to associate ingested requests with")
    staff_id: int = Field(..., description="Staff member performing the ingestion")
    raw_text: str = Field(
        ...,
        description="Raw text of the discovery requests, pasted or extracted from a PDF",
    )


class DiscoveryRequestResponse(BaseModel):
    """Response for a single discovery request item."""
    id: int
    matter_id: int
    request_type: DiscoveryRequestType
    request_number: int
    source_text: str
    status: DiscoveryRequestStatus
    ingested_by_staff_id: int


class DiscoveryIngestResponse(BaseModel):
    """Response from POST /api/v1/discovery/ingest — items are previews pending commit."""
    matter_id: int
    item_count: int = Field(..., description="Number of items parsed from the raw text")
    items: list[DiscoveryRequestResponse]


class DiscoveryResponseUpdateRequest(BaseModel):
    """
    Body for PATCH /api/v1/discovery/responses/{id}.

    Clients submit ``client_response_text`` and ``rfa_selection``.
    Attorneys/paralegals may additionally set ``attorney_objection``,
    ``privilege_claimed``, ``attorney_note``, ``final_response_text``,
    and ``is_final``.
    """
    client_response_text: Optional[str] = None
    rfa_selection: Optional[RFASelection] = None
    has_responsive_documents: Optional[bool] = None
    attorney_objection: Optional[str] = None
    privilege_claimed: Optional[bool] = None
    attorney_note: Optional[str] = None
    final_response_text: Optional[str] = None
    is_final: Optional[bool] = None


class DiscoveryResponseSchema(BaseModel):
    """Response body for a discovery response record."""
    id: int
    discovery_request_id: int
    client_response_text: Optional[str]
    rfa_selection: Optional[RFASelection]
    has_responsive_documents: Optional[bool]
    attorney_objection: Optional[str]
    privilege_claimed: bool
    attorney_note: Optional[str]
    final_response_text: Optional[str]
    is_final: bool
    last_updated_by_uid: Optional[str]

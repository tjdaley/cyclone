"""
app/schemas/discovery.py - Request and response schemas for discovery endpoints.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from db.models.discovery import (
    DiscoveryRequestStatus,
    DocumentRequestType,
    RFASelection,
)


# ── Discovery Document (parent) ──────────────────────────────────────────────

class DiscoveryDocumentResponse(BaseModel):
    """Response for a discovery_requests (parent document) record."""
    id: int
    matter_id: int
    ingested_by_staff_id: int
    propounded_date: date
    due_date: date
    request_type: DocumentRequestType
    look_back_date: Optional[date]
    response_served_date: Optional[date]
    storage_path: Optional[str] = None


class DiscoveryDocumentUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/discovery/documents/{id}."""
    propounded_date: Optional[date] = None
    due_date: Optional[date] = None
    look_back_date: Optional[date] = None
    response_served_date: Optional[date] = None


# ── Discovery Request Items ──────────────────────────────────────────────────

class DiscoveryRequestItemResponse(BaseModel):
    """Response for a single discovery_request_items record."""
    id: int
    discovery_request_id: int
    matter_id: int
    request_number: int
    source_text: str
    status: DiscoveryRequestStatus
    ingested_by_staff_id: int
    interpretations: list[str]
    privileges: list[dict]
    objections: list[dict]
    client_response_needed: bool
    response: Optional[str] = None


class DiscoveryRequestItemUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/discovery/items/{item_id}."""
    source_text: Optional[str] = None
    client_response_needed: Optional[bool] = None
    interpretations: Optional[list[str]] = None
    privileges: Optional[list[dict]] = None
    objections: Optional[list[dict]] = None
    response: Optional[str] = None


# ── Standard lookups ─────────────────────────────────────────────────────────

class StandardPrivilegeResponse(BaseModel):
    """Response for a standard_privileges row."""
    id: int
    slug: str
    text: str


class StandardObjectionResponse(BaseModel):
    """Response for a standard_objections row."""
    id: int
    slug: str
    applies_to: list[str]
    text: str


# ── Upload / Ingestion ───────────────────────────────────────────────────────

class DiscoveryUploadResponse(BaseModel):
    """Response from POST /api/v1/discovery/upload — full ingestion result."""
    document: DiscoveryDocumentResponse
    item_count: int
    items: list[DiscoveryRequestItemResponse]
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues during ingestion")


# ── Discovery Responses (unchanged — per-item responses) ─────────────────────

class DiscoveryResponseUpdateRequest(BaseModel):
    """Body for PATCH /api/v1/discovery/responses/{id}."""
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

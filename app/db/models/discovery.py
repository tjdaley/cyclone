"""
app/db/models/discovery.py - Domain and database models for discovery.

Two-level structure:
- DiscoveryDocument (discovery_requests table) — the served document
- DiscoveryRequestItem (discovery_request_items table) — one numbered request within that document

The old DiscoveryRequest model has been renamed to DiscoveryRequestItem.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class DocumentRequestType(str, Enum):
    """Type of the discovery request document (the parent batch)."""
    interrogatories = "interrogatories"
    production = "production"
    disclosures = "disclosures"
    admissions = "admissions"


class DiscoveryRequestStatus(str, Enum):
    """Workflow status of an individual discovery request item."""
    pending_client = "pending_client"
    pending_review = "pending_review"
    finalized = "finalized"
    objected = "objected"


class RFASelection(str, Enum):
    """Client selection for a Request for Admission."""
    admit = "admit"
    deny = "deny"
    insufficient_information = "insufficient_information"


# ── Discovery Document (parent — the served document) ─────────────────────────

class DiscoveryDocument(BaseModel):
    """
    Parent model for a discovery request document — the set of requests
    served on our client by opposing counsel.

    One document contains many DiscoveryRequestItem records.
    """
    matter_id: int = Field(..., description="FK to matters table")
    ingested_by_staff_id: int = Field(..., description="FK to staff member who ingested this document")
    propounded_date: date = Field(..., description="Date the document was served on our client")
    due_date: date = Field(..., description="Date our responses are due")
    request_type: DocumentRequestType = Field(..., description="Category of discovery requests in this document")
    look_back_date: Optional[date] = Field(
        default=None,
        description="Earliest date for responsive documents, if specified in the preamble",
    )
    response_served_date: Optional[date] = Field(
        default=None,
        description="Date we served our response, if completed",
    )
    storage_path: Optional[str] = Field(
        default=None,
        description="Supabase Storage path to the original PDF",
    )


class DiscoveryDocumentInDB(DiscoveryDocument):
    """Database model for the discovery_requests table."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by the database on update")
    model_config = ConfigDict(from_attributes=True)


# ── Discovery Request Item (individual numbered request) ──────────────────────

class DiscoveryRequestItem(BaseModel):
    """
    One numbered request within a discovery document.

    Formerly ``DiscoveryRequest`` — renamed to reflect the two-level structure
    where ``discovery_requests`` is the parent document table.
    """
    discovery_request_id: int = Field(..., description="FK to the parent discovery_requests record")
    matter_id: int = Field(..., description="Denormalized FK to matters for efficient lookup")
    request_number: int = Field(..., ge=1, description="Sequential number from opposing counsel")
    source_text: str = Field(..., description="Verbatim request text as markdown, preserving original typography")
    status: DiscoveryRequestStatus = Field(
        default=DiscoveryRequestStatus.pending_client,
        description="Workflow status of this item",
    )
    ingested_by_staff_id: int = Field(..., description="FK to staff member who ingested this item")
    interpretations: list[str] = Field(
        default_factory=list,
        description="Attorney interpretations of this request",
    )
    privileges: list[dict] = Field(
        default_factory=list,
        description="Privilege assertions: [{privilege_name: str, text: str}]",
    )
    objections: list[dict] = Field(
        default_factory=list,
        description="Objections: [{objection_name: str, text: str}]",
    )
    client_response_needed: bool = Field(
        default=True,
        description="Whether the client must provide a substantive response",
    )
    response: Optional[str] = Field(
        default=None,
        description="Attorney's formal response to this request, in markdown",
    )


class DiscoveryRequestItemInDB(DiscoveryRequestItem):
    """Database model for the discovery_request_items table."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by the database on update")
    model_config = ConfigDict(from_attributes=True)


# ── Discovery Response (one response per item) ───────────────────────────────

class DiscoveryResponse(BaseModel):
    """
    Response to a single discovery request item.

    Drafted by the client, then reviewed, objected, and finalized by the attorney.
    """
    discovery_request_id: int = Field(..., description="FK to discovery_request_items")
    client_response_text: Optional[str] = Field(default=None, description="Client's draft response")
    rfa_selection: Optional[RFASelection] = Field(default=None, description="RFA selection (admissions only)")
    has_responsive_documents: Optional[bool] = Field(default=None, description="Whether responsive docs exist (production only)")
    attorney_objection: Optional[str] = Field(default=None, description="Attorney objection text")
    privilege_claimed: bool = Field(default=False, description="Whether a privilege is asserted")
    attorney_note: Optional[str] = Field(default=None, description="Internal note — not part of the formal response")
    final_response_text: Optional[str] = Field(default=None, description="Attorney's final edited response text")
    is_final: bool = Field(default=False, description="True once the attorney marks this response as final")
    last_updated_by_uid: Optional[str] = Field(default=None, description="Supabase Auth UID of the last editor")


class DiscoveryResponseInDB(DiscoveryResponse):
    """Database model for the discovery_responses table."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None, description="Set by the database on update")
    model_config = ConfigDict(from_attributes=True)


# ── Standard lookup tables ────────────────────────────────────────────────────

class StandardPrivilege(BaseModel):
    """Read-only lookup row from the standard_privileges table."""
    id: int = Field(..., description="Primary key")
    slug: str = Field(..., description="Unique key, e.g. 'attorney-client'")
    text: str = Field(..., description="Template text in markdown")
    model_config = ConfigDict(from_attributes=True)


class StandardObjection(BaseModel):
    """Read-only lookup row from the standard_objections table."""
    id: int = Field(..., description="Primary key")
    slug: str = Field(..., description="Unique key, e.g. 'relevance'")
    applies_to: list[str] = Field(..., description="Request types this objection applies to, or ['*'] for all")
    text: str = Field(..., description="Template text in markdown")
    model_config = ConfigDict(from_attributes=True)

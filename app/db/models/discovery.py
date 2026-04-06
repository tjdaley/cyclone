"""
app/db/models/discovery.py - Domain and database models for discovery requests and responses.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryRequestType(str, Enum):
    """Type of discovery request."""
    interrogatory = "interrogatory"
    rfa = "rfa"              # Request for Admission
    rfp = "rfp"              # Request for Production
    witness_list = "witness_list"


class DiscoveryRequestStatus(str, Enum):
    """Workflow status of a discovery request."""
    pending_client = "pending_client"        # Awaiting client input
    pending_review = "pending_review"        # Client submitted; awaiting attorney review
    finalized = "finalized"                  # Attorney marked as final
    objected = "objected"                    # Fully objected; no substantive response


class DiscoveryRequest(BaseModel):
    """
    Domain model for a single discovery request item.

    Ingested by the LLM discovery parser (POST /api/v1/discovery/ingest).
    Each item is stored separately so clients and attorneys can work
    through them individually.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    request_type: DiscoveryRequestType = Field(
        ...,
        description="Type of discovery request: interrogatory, rfa, rfp, or witness_list",
    )
    request_number: int = Field(
        ...,
        ge=1,
        description="Sequential number assigned by opposing counsel, e.g. Interrogatory No. 1",
    )
    source_text: str = Field(
        ...,
        description="Verbatim text of the discovery request as received from opposing counsel",
    )
    status: DiscoveryRequestStatus = Field(
        default=DiscoveryRequestStatus.pending_client,
        description="Workflow status tracking client and attorney progress on this item",
    )
    ingested_by_staff_id: int = Field(
        ...,
        description="Foreign key to the staff member who ingested this discovery batch",
    )


class DiscoveryRequestInDB(DiscoveryRequest):
    """Database model — extends DiscoveryRequest with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)


class RFASelection(str, Enum):
    """Client's selection for a Request for Admission."""
    admit = "admit"
    deny = "deny"
    insufficient_information = "insufficient_information"


class DiscoveryResponse(BaseModel):
    """
    Domain model for a discovery response.

    One response record per discovery_request. Combines the client's draft
    with attorney edits, objections, and finalization in a single record
    for simplicity.

    Witness list items are handled by a separate ``WitnessEntry`` model
    (future) linked to the ``discovery_request_id``.
    """
    discovery_request_id: int = Field(
        ...,
        description="Foreign key to the discovery_requests table",
    )
    client_response_text: Optional[str] = Field(
        default=None,
        description="Client's draft response text (interrogatories and RFPs)",
    )
    rfa_selection: Optional[RFASelection] = Field(
        default=None,
        description="Client's admission/denial selection (RFAs only)",
    )
    has_responsive_documents: Optional[bool] = Field(
        default=None,
        description="Whether the client has documents responsive to this RFP",
    )
    attorney_objection: Optional[str] = Field(
        default=None,
        description="Attorney's formal objection text, if any",
    )
    privilege_claimed: bool = Field(
        default=False,
        description="True if attorney-client or work-product privilege is asserted",
    )
    attorney_note: Optional[str] = Field(
        default=None,
        description="Attorney's interpretive note or instruction to the client (not part of the formal response)",
    )
    final_response_text: Optional[str] = Field(
        default=None,
        description="Attorney's final edited response text, ready for service",
    )
    is_final: bool = Field(
        default=False,
        description="True once the attorney has marked this response ready for service",
    )
    last_updated_by_uid: Optional[str] = Field(
        default=None,
        description="Supabase Auth UID of the user who last modified this response",
    )


class DiscoveryResponseInDB(DiscoveryResponse):
    """Database model — extends DiscoveryResponse with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

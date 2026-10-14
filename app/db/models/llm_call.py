"""
app/db/models/llm_call.py - One vendor call, as the vendor counted it.

An append-only cost ledger (migration 039). Every attempt a failover chain makes
is a row, including attempts that failed: a call that timed out after the vendor
had started generating, or whose reply was rejected, still costs money.

Immutable like ``audit_log``, so the InDB model has no ``updated_at``.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LlmCall(BaseModel):
    """A single call to a model vendor, with who it was for and what it used."""

    occurred_at: datetime = Field(
        ..., description="When the call started. Set by the application, not the insert")

    firm_id: Optional[int] = Field(
        default=None,
        description="Firm the spend belongs to. None leaves the database default (the one "
                    "firm that exists) in place")
    staff_id: Optional[int] = Field(
        default=None, description="Staff member the work was done for, when known. Not a FK")
    matter_id: Optional[int] = Field(
        default=None, description="Matter the work was done for, when known. Not a FK")
    auth_uid: Optional[str] = Field(
        default=None,
        description="Signed-in identity of a web request; resolve through staff.supabase_uid")
    source_type: str = Field(
        ..., description="What started the work: job, request, crm, crm_lead, unattributed")
    source_id: Optional[str] = Field(
        default=None, description="Job id, request route, CRM step, or lead session")

    profile: str = Field(..., description="Task profile the caller named")
    call_type: str = Field(..., description="'text' or 'vision'")
    vendor: str = Field(..., description="Vendor that was called")
    model: str = Field(..., description="Model that was REQUESTED, as named in the profile catalog")
    served_model: Optional[str] = Field(
        default=None,
        description="Model the vendor's response says served the call. Pricing uses this "
                    "when present (042). None when no response arrived or none was reported")
    vendor_response_id: Optional[str] = Field(
        default=None,
        description="The vendor's own id for the response -- what its billing support looks up")
    chain_position: int = Field(
        ..., description="Position in the profile's chain; above 1 means failover")
    outcome: str = Field(..., description="'succeeded' or 'failed'")
    error_class: Optional[str] = Field(
        default=None,
        description="Exception class on failure. Never the message, which can quote the prompt")
    duration_ms: Optional[int] = Field(default=None, description="Wall time of the vendor call")
    stop_reason: Optional[str] = Field(
        default=None,
        description="Why the vendor stopped generating, in its own word (046). None when no "
                    "response arrived or none was reported")
    stop_reason_class: Optional[str] = Field(
        default=None,
        description="stop_reason reconciled across vendors: complete, truncated, refused, "
                    "or other. What tells 'it ran out of room' from 'it decided it was done'")

    input_uncached_tokens: Optional[int] = Field(
        default=None, description="Input billed at the full rate. None means not reported")
    input_cache_read_tokens: Optional[int] = Field(
        default=None, description="Input served from the vendor's prompt cache")
    input_cache_write_tokens: Optional[int] = Field(
        default=None, description="Input written into the prompt cache (Anthropic only)")
    output_tokens: Optional[int] = Field(
        default=None, description="Everything billed at the output rate, reasoning included")
    reasoning_tokens: Optional[int] = Field(
        default=None, description="Reasoning share of output_tokens, where reported")
    usage_raw: Optional[dict[str, Any]] = Field(
        default=None, description="The vendor's usage object verbatim, for re-normalizing")


class LlmCallInDB(LlmCall):
    """Database model. Immutable, so no updated_at."""

    id: int = Field(..., description="Primary key, set by database")
    created_at: datetime = Field(..., description="Set by database")
    model_config = ConfigDict(from_attributes=True)

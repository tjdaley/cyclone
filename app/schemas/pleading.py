"""
app/schemas/pleading.py - Request and response schemas for pleadings, claims,
opposing counsel, and matter children.
"""
from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from db.models.pleading import ChildSex, ClaimKind, CounselRole
from db.models.staff import FullName


# ── Matter Children ───────────────────────────────────────────────────────────

class MatterChildRequest(BaseModel):
    name: FullName
    date_of_birth: date
    sex: ChildSex
    needs_support_after_majority: bool = False


class MatterChildResponse(BaseModel):
    id: int
    matter_id: int
    name: FullName
    date_of_birth: date
    sex: ChildSex
    needs_support_after_majority: bool


# ── Opposing Counsel ──────────────────────────────────────────────────────────

class OpposingCounselRequest(BaseModel):
    name: FullName
    firm_name: Optional[str] = None
    street_address: Optional[str] = None
    street_address_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    email: Optional[str] = None
    cell_phone: Optional[str] = None
    telephone: Optional[str] = None
    fax: Optional[str] = None
    bar_state: str
    bar_number: str
    email_ccs: list[str] = Field(default_factory=list)


class OpposingCounselUpdateRequest(BaseModel):
    name: Optional[FullName] = None
    firm_name: Optional[str] = None
    street_address: Optional[str] = None
    street_address_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    email: Optional[str] = None
    cell_phone: Optional[str] = None
    telephone: Optional[str] = None
    fax: Optional[str] = None
    email_ccs: Optional[list[str]] = None


class OpposingCounselResponse(BaseModel):
    id: int
    name: FullName
    firm_name: Optional[str]
    street_address: Optional[str]
    street_address_2: Optional[str]
    city: Optional[str]
    state: Optional[str]
    postal_code: Optional[str]
    email: Optional[str]
    cell_phone: Optional[str]
    telephone: Optional[str]
    fax: Optional[str]
    bar_state: str
    bar_number: str
    email_ccs: list[str]


class MatterOpposingCounselResponse(BaseModel):
    id: int
    matter_id: int
    opposing_counsel_id: int
    opposing_party_id: Optional[int]
    role: CounselRole
    started_date: Optional[date]
    ended_date: Optional[date]


# ── Matter Pleadings ──────────────────────────────────────────────────────────

class MatterPleadingResponse(BaseModel):
    id: int
    matter_id: int
    opposing_party_id: Optional[int]
    title: str
    filed_date: Optional[date]
    served_date: Optional[date]
    amends_pleading_id: Optional[int]
    is_supplement: bool
    storage_path: Optional[str]
    ingested_by_staff_id: int


class MatterPleadingUpdateRequest(BaseModel):
    title: Optional[str] = None
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    amends_pleading_id: Optional[int] = None
    is_supplement: Optional[bool] = None
    opposing_party_id: Optional[int] = None


# ── Matter Claims ─────────────────────────────────────────────────────────────

class MatterClaimResponse(BaseModel):
    id: int
    matter_pleading_id: int
    matter_id: int
    opposing_party_id: Optional[int]
    kind: ClaimKind
    label: str
    narrative: str
    statute_rule_cited: Optional[str]


class MatterClaimUpdateRequest(BaseModel):
    kind: Optional[ClaimKind] = None
    label: Optional[str] = None
    narrative: Optional[str] = None
    statute_rule_cited: Optional[str] = None
    opposing_party_id: Optional[int] = None


# ── Pleading Ingestion: Preview ──────────────────────────────────────────────

class FieldDiff(BaseModel):
    """A proposed change to a matter field."""
    current: Optional[Any] = None
    proposed: Optional[Any] = None


class ChildPreview(BaseModel):
    """A child extracted by the LLM, not yet committed."""
    name: FullName
    date_of_birth: Optional[date] = None
    sex: Optional[ChildSex] = None
    needs_support_after_majority: bool = False


class OCPreview(BaseModel):
    """An opposing counsel extracted by the LLM."""
    name: FullName
    firm_name: Optional[str] = None
    street_address: Optional[str] = None
    street_address_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    email: Optional[str] = None
    cell_phone: Optional[str] = None
    telephone: Optional[str] = None
    fax: Optional[str] = None
    bar_state: Optional[str] = None
    bar_number: Optional[str] = None
    email_ccs: list[str] = Field(default_factory=list)


class OCMatchPreview(BaseModel):
    """An OC that matched an existing row by bar number, with proposed diffs."""
    existing_id: int
    existing: OpposingCounselResponse
    proposed: OCPreview
    diffs: dict[str, FieldDiff] = Field(default_factory=dict)


class ClaimPreview(BaseModel):
    """A claim extracted by the LLM."""
    kind: ClaimKind
    label: str
    narrative: str
    statute_rule_cited: Optional[str] = None
    # Whose claim: 'our_client' or 'opposing' (frontend can assign specific party_id)
    party_side: str = "opposing"


class PleadingPreview(BaseModel):
    """Pleading metadata extracted by the LLM."""
    title: str
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    is_supplement: bool = False
    amends_pleading_title: Optional[str] = None  # Hint from the LLM, not a FK yet


class PleadingIngestPreviewResponse(BaseModel):
    """Full preview payload returned from POST /pleadings/preview."""
    matter_id: int
    raw_text: str  # Echoed back so frontend can include it in commit
    pleading: PleadingPreview
    matter_field_updates: dict[str, FieldDiff] = Field(default_factory=dict)
    new_children: list[ChildPreview] = Field(default_factory=list)
    opposing_counsel_matches: list[OCMatchPreview] = Field(default_factory=list)
    new_opposing_counsel: list[OCPreview] = Field(default_factory=list)
    claims: list[ClaimPreview] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── Pleading Ingestion: Commit ───────────────────────────────────────────────

class ClaimCommitEntry(BaseModel):
    """A claim to create, reviewed/edited by the attorney."""
    kind: ClaimKind
    label: str
    narrative: str
    statute_rule_cited: Optional[str] = None
    opposing_party_id: Optional[int] = None  # resolved to a specific OP id, or null for our client


class OCCommitEntry(BaseModel):
    """
    An OC to create or update, reviewed by the attorney.

    If existing_id is set, this is an UPDATE (merge fields into existing row).
    Otherwise it's a CREATE.
    """
    existing_id: Optional[int] = None
    name: FullName
    firm_name: Optional[str] = None
    street_address: Optional[str] = None
    street_address_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    email: Optional[str] = None
    cell_phone: Optional[str] = None
    telephone: Optional[str] = None
    fax: Optional[str] = None
    bar_state: str
    bar_number: str
    email_ccs: list[str] = Field(default_factory=list)
    # Matter-level association
    opposing_party_id: Optional[int] = None
    role: CounselRole = CounselRole.lead


class ChildCommitEntry(BaseModel):
    """A child to create."""
    name: FullName
    date_of_birth: date
    sex: ChildSex
    needs_support_after_majority: bool = False


class PleadingCommitRequest(BaseModel):
    """
    Body for POST /api/v1/pleadings/commit.

    This is the attorney-reviewed version of PleadingIngestPreviewResponse,
    with any edits, removals, or additions.
    """
    matter_id: int
    raw_text: str

    # Pleading metadata
    title: str
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    opposing_party_id: Optional[int] = None  # null = our client's pleading
    is_supplement: bool = False
    amends_pleading_id: Optional[int] = None  # resolved FK, selected by attorney

    # Matter field updates to apply (only the accepted ones)
    matter_field_updates: dict[str, Any] = Field(default_factory=dict)

    # Children, OC, and claims — all already reviewed
    children: list[ChildCommitEntry] = Field(default_factory=list)
    opposing_counsel: list[OCCommitEntry] = Field(default_factory=list)
    claims: list[ClaimCommitEntry] = Field(default_factory=list)


class PleadingCommitResponse(BaseModel):
    """Response from POST /api/v1/pleadings/commit — records created."""
    pleading: MatterPleadingResponse
    children_created: int
    opposing_counsel_linked: int
    claims_created: int

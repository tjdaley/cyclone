"""
app/schemas/pleading.py - Request and response schemas for pleadings, claims,
opposing counsel, and matter children.
"""
from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from db.models.pleading import ChildSex, ClaimKind, CounselRole, PleadingStatus
from db.models.staff import FullName


# ── Matter Children ───────────────────────────────────────────────────────────

class MatterChildRequest(BaseModel):
    name: FullName
    date_of_birth: date
    sex: ChildSex
    needs_support_after_majority: bool = False


class MatterChildUpdateRequest(BaseModel):
    """Partial update of a child. Omitted fields are left as-is."""
    name: Optional[FullName] = None
    date_of_birth: Optional[date] = None
    sex: Optional[ChildSex] = None
    needs_support_after_majority: Optional[bool] = None


class MatterChildResponse(BaseModel):
    id: int
    matter_id: int
    name: FullName
    date_of_birth: date
    sex: ChildSex
    needs_support_after_majority: bool


# ── Opposing Counsel ──────────────────────────────────────────────────────────

# ── Opposing Parties ─────────────────────────────────────────────────────────

class OpposingPartyPreview(BaseModel):
    """An adverse party extracted by the LLM, not yet committed."""
    existing_id: Optional[int] = Field(
        default=None,
        description="Set when this party is already on the matter; the commit reuses that row",
    )
    full_name: str = Field(..., description="Party name as written in the pleading")
    relationship: Optional[str] = Field(default=None, description="Relationship to our client, e.g. 'spouse'")


class OpposingPartyCommitEntry(BaseModel):
    """An adverse party to create, or to reuse when ``existing_id`` is set."""
    existing_id: Optional[int] = None
    full_name: str
    relationship: Optional[str] = None


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


class MatterOpposingCounselLinkRequest(BaseModel):
    """Attach an existing opposing counsel record to a matter."""
    opposing_counsel_id: int = Field(..., description="FK to an existing opposing_counsel row")
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="Which opposing party this counsel represents on this matter",
    )
    role: CounselRole = Field(default=CounselRole.lead, description="Counsel's role on this matter")
    started_date: Optional[date] = Field(default=None, description="When counsel appeared")
    ended_date: Optional[date] = Field(default=None, description="When counsel withdrew, if applicable")


class MatterOpposingCounselUpdateRequest(BaseModel):
    """
    Partial update of the matter↔counsel association only.

    The counsel's own contact details are edited through
    PATCH /opposing-counsel/{oc_id}, since that record is shared across matters.
    """
    opposing_party_id: Optional[int] = None
    role: Optional[CounselRole] = None
    started_date: Optional[date] = None
    ended_date: Optional[date] = None


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
    status: PleadingStatus
    storage_path: Optional[str]
    ingested_by_staff_id: int


class SignedUrlResponse(BaseModel):
    """A short-lived URL for a stored file."""
    url: str = Field(..., description="Signed URL; the signature is the authorization")
    expires_in: int = Field(..., description="Seconds the URL remains valid")


class MatterPleadingUpdateRequest(BaseModel):
    title: Optional[str] = None
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    amends_pleading_id: Optional[int] = None
    is_supplement: Optional[bool] = None
    status: Optional[PleadingStatus] = Field(
        default=None,
        description="live | superseded | withdrawn | inactive",
    )
    opposing_party_id: Optional[int] = None


# ── Matter Claims ─────────────────────────────────────────────────────────────

class MatterClaimCreateRequest(BaseModel):
    """
    Add a claim to a matter by hand.

    ``matter_pleading_id`` is required because every claim is pleaded
    somewhere — it must name a pleading already on this matter.
    """
    matter_pleading_id: int = Field(..., description="Pleading this claim appears in; must belong to the matter")
    kind: ClaimKind = Field(..., description="Type of legal position")
    label: str = Field(..., description="Short descriptive label, e.g. 'Fault: adultery'")
    narrative: str = Field(..., description="Full text of the claim as pleaded")
    statute_rule_cited: Optional[str] = Field(default=None, description="Statute or rule cited in support")
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="Whose claim this is. Null means our client's.",
    )


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
    existing_id: Optional[int] = Field(
        default=None,
        description="Set when this child is already on the matter (matched by name + date of "
                    "birth). The commit updates that row instead of inserting a second one.",
    )
    name: FullName
    date_of_birth: Optional[date] = None
    sex: Optional[ChildSex] = None
    needs_support_after_majority: bool = False


class OCPreview(BaseModel):
    """An opposing counsel extracted by the LLM."""
    represents: Optional[str] = Field(
        default=None,
        description="Party this attorney represents, as named in the pleading. Review-only — "
                    "it is how the attorney confirms the right lawyer was picked up, and is "
                    "not persisted on the opposing_counsel row.",
    )
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
    opposing_parties: list[OpposingPartyPreview] = Field(default_factory=list)
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
    represents: Optional[str] = Field(
        default=None,
        description="Party name this attorney represents. Used to resolve opposing_party_id "
                    "when the party is being created by this same commit and has no id yet.",
    )
    role: CounselRole = CounselRole.lead


class ChildCommitEntry(BaseModel):
    """A child to create, or to update when ``existing_id`` is set."""
    existing_id: Optional[int] = Field(
        default=None,
        description="Existing matter_children.id this entry refers to; None creates a new row",
    )
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
    opposing_party_name: Optional[str] = Field(
        default=None,
        description="Filing party by name, for a party created by this same commit and so "
                    "having no id yet. Ignored when opposing_party_id is set.",
    )
    is_supplement: bool = False
    amends_pleading_id: Optional[int] = None  # resolved FK, selected by attorney

    # Matter field updates to apply (only the accepted ones)
    matter_field_updates: dict[str, Any] = Field(default_factory=dict)

    # Parties, children, OC, and claims — all already reviewed
    opposing_parties: list[OpposingPartyCommitEntry] = Field(default_factory=list)
    children: list[ChildCommitEntry] = Field(default_factory=list)
    opposing_counsel: list[OCCommitEntry] = Field(default_factory=list)
    claims: list[ClaimCommitEntry] = Field(default_factory=list)


class PleadingCommitResponse(BaseModel):
    """Response from POST /api/v1/pleadings/commit — records created."""
    pleading: MatterPleadingResponse
    opposing_parties_created: int
    children_created: int
    opposing_counsel_linked: int
    claims_created: int

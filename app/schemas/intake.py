"""
app/schemas/intake.py - Request and response schemas for matter intake from a pleading.

Intake runs *before* we know who our client is, which is what separates it from
the pleading ingestion in schemas/pleading.py. Extraction reports every party
and every attorney with no notion of "opposing"; the attorney names our side in
the review step, and everything adverse follows from that one answer.
"""
from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from db.models.matter import DiscoveryLevel, MatterType
from db.models.staff import FullName
from schemas.pleading import ChildCommitEntry, ChildPreview, ClaimCommitEntry, ClaimPreview


# ── Preview ──────────────────────────────────────────────────────────────────

class IntakeClientMatch(BaseModel):
    """An existing client who may be the party named in the pleading."""
    client_id: int = Field(..., description="Existing clients.id")
    full_name: str = Field(..., description="Client's name as recorded")
    confidence: str = Field(..., description="'strong' (every word matches) or 'partial' (surname only)")


class IntakeLeadMatch(BaseModel):
    """
    An existing lead who may be the party named in the pleading.

    Preferred over creating a client from the caption: a lead has already been
    through the conflict check, and carries contact details a pleading never
    does. Promoting one links the lead to the client it becomes.
    """
    session_uuid: UUID = Field(..., description="Lead identifier; promotion posts to this")
    full_name: str = Field(..., description="Lead's name as captured")
    email: Optional[str] = None
    telephone: Optional[str] = None
    status: str = Field(..., description="Where the lead sits in the pipeline")
    confidence: str = Field(..., description="'strong' or 'partial', same rule as client matching")


class IntakePartyPreview(BaseModel):
    """A party to the suit, before we know which one is ours."""
    full_name: str = Field(..., description="Party name as written in the pleading")
    designation: Optional[str] = Field(
        default=None,
        description="Role in the caption, e.g. 'petitioner', 'respondent', 'counterpetitioner'",
    )
    client_matches: list[IntakeClientMatch] = Field(
        default_factory=lambda: list[IntakeClientMatch](),
        description="Existing clients this party may already be",
    )
    lead_matches: list[IntakeLeadMatch] = Field(
        default_factory=lambda: list[IntakeLeadMatch](),
        description="Unconverted leads this party may be. Promoting one is preferred to "
                    "creating a client from the caption.",
    )


class IntakeAttorneyPreview(BaseModel):
    """An attorney of record, with the party they represent."""
    represents: Optional[str] = Field(default=None, description="Party name this attorney appears for")
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


class IntakeCasePreview(BaseModel):
    """The case style and the pleading's own details."""
    title: str = Field(..., description="Title of the pleading")
    filed_date: Optional[date] = None
    served_date: Optional[date] = None
    state: Optional[str] = None
    county: Optional[str] = None
    court_name: Optional[str] = None
    matter_number: Optional[str] = None
    matter_type: Optional[MatterType] = None
    discovery_level: Optional[DiscoveryLevel] = None
    suggested_matter_name: Optional[str] = Field(
        default=None,
        description="Proposed matter name built from the parties, e.g. 'Salmons v. Salmons'",
    )


class MatterIntakePreviewResponse(BaseModel):
    """Everything the review screen needs. Nothing has been written."""
    raw_text: str = Field(..., description="Echoed back so commit can store it on the pleading")
    case: IntakeCasePreview
    parties: list[IntakePartyPreview] = Field(
        default_factory=lambda: list[IntakePartyPreview](),
    )
    attorneys: list[IntakeAttorneyPreview] = Field(
        default_factory=lambda: list[IntakeAttorneyPreview](),
    )
    children: list[ChildPreview] = Field(
        default_factory=lambda: list[ChildPreview](),
    )
    claims: list[ClaimPreview] = Field(
        default_factory=lambda: list[ClaimPreview](),
    )
    warnings: list[str] = Field(
        default_factory=lambda: list[str](),
        description="Non-fatal issues found during extraction",
    )


# ── Async extraction ─────────────────────────────────────────────────────────

class IntakeJobResponse(BaseModel):
    """
    A queued or finished intake extraction.

    Reading a pleading is one LLM vision call per image-only page plus two more
    calls over the text, which is far too long to hold an HTTP request open —
    so the upload returns one of these and the caller polls it.
    """
    id: str = Field(..., description="Job id to poll")
    status: str = Field(..., description="queued | running | succeeded | failed")
    result: Optional[MatterIntakePreviewResponse] = Field(
        default=None,
        description="The extraction, once status is 'succeeded'",
    )
    error: Optional[str] = Field(default=None, description="Why it failed, when status is 'failed'")


# ── Commit ───────────────────────────────────────────────────────────────────

class IntakeNewClient(BaseModel):
    """
    A client to create from the pleading.

    The pleading supplies only a name; everything else is required by the
    clients table and has to be collected in the review step.
    """
    name: FullName
    auth_email: str
    email: str
    telephone: str
    referral_type: str
    referral_source: str
    notes: Optional[str] = None


class IntakeMatterFields(BaseModel):
    """Matter fields as reviewed by the attorney."""
    matter_name: str
    short_name: Optional[str] = None
    matter_type: MatterType
    state: str = "Texas"
    county: str
    court_name: Optional[str] = None
    matter_number: Optional[str] = None
    discovery_level: Optional[DiscoveryLevel] = None
    opened_date: Optional[date] = None
    notes: Optional[str] = None


class IntakePartyCommitEntry(BaseModel):
    """A party as reviewed. Includes ours — the service drops it by name."""
    full_name: str
    designation: Optional[str] = None


class IntakeAttorneyCommitEntry(IntakeAttorneyPreview):
    """
    An attorney as reviewed.

    Only those adverse to our client are stored; our own counsel is dropped.
    bar_state/bar_number are required to store one, since they are the dedup key.
    """


class MatterIntakeCommitRequest(BaseModel):
    """
    Body for POST /api/v1/matters/intake/commit.

    Creates (optionally) a client, then a matter, then — when a pleading was
    supplied — runs the ordinary pleading commit against it.

    ``case`` is optional so the same path serves promoting a lead with no
    pleading in hand: client and matter are still created, there is simply no
    pleading, and no parties or counsel to derive from one.
    """
    raw_text: str = ""
    our_party_name: Optional[str] = Field(
        default=None,
        description="Which party we represent; everything else on the pleading is adverse. "
                    "Only meaningful when a pleading was supplied.",
    )

    existing_client_id: Optional[int] = Field(default=None, description="Use this client instead of creating one")
    new_client: Optional[IntakeNewClient] = Field(default=None, description="Client to create when there is no match")

    matter: IntakeMatterFields
    case: Optional[IntakeCasePreview] = Field(
        default=None,
        description="The pleading being filed with this matter. Omit to open a matter without one.",
    )

    parties: list[IntakePartyCommitEntry] = Field(
        default_factory=lambda: list[IntakePartyCommitEntry](),
    )
    attorneys: list[IntakeAttorneyCommitEntry] = Field(
        default_factory=lambda: list[IntakeAttorneyCommitEntry](),
    )
    children: list[ChildCommitEntry] = Field(
        default_factory=lambda: list[ChildCommitEntry](),
    )
    claims: list[ClaimCommitEntry] = Field(
        default_factory=lambda: list[ClaimCommitEntry](),
    )


class MatterIntakeCommitResponse(BaseModel):
    """What intake created."""
    client_id: int
    client_created: bool
    matter_id: int
    pleading_id: Optional[int] = Field(default=None, description="None when no pleading was supplied")
    opposing_parties_created: int
    children_created: int
    opposing_counsel_linked: int
    claims_created: int

"""
app/db/models/matter.py - Domain and database models for legal matters.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MatterStatus(str, Enum):
    """Lifecycle status of a matter."""
    intake = "intake"
    conflict_review = "conflict_review"
    active = "active"
    closed = "closed"
    archived = "archived"


class DiscoveryLevel(str, Enum):
    """Texas Rules of Civil Procedure 190 discovery levels."""
    level_1 = "level_1"
    level_2 = "level_2"
    level_3 = "level_3"


class ClientAlignment(str, Enum):
    """
    Which side our client is on, in the vocabulary a caption uses.

    This titles every exhibit the matter produces — "Petitioner's Financial
    Summary" — so it is caption language, not an internal role. The counter-
    forms appear once a counter-petition is filed and the same person is both
    respondent and counter-petitioner; the caption names the posture the
    exhibit is offered in.
    """
    petitioner = "petitioner"
    respondent = "respondent"
    counter_petitioner = "counter_petitioner"
    counter_respondent = "counter_respondent"
    intervenor = "intervenor"
    plaintiff = "plaintiff"
    defendant = "defendant"
    applicant = "applicant"
    movant = "movant"
    other = "other"

    @property
    def caption(self) -> str:
        """How the alignment is written on a document."""
        return _ALIGNMENT_CAPTIONS[self]


# Hyphenated in a caption, underscored in the database. Spelled out rather than
# derived from the enum name so "Counter-Petitioner" keeps its hyphen and its
# interior capital.
_ALIGNMENT_CAPTIONS = {
    ClientAlignment.petitioner: "Petitioner",
    ClientAlignment.respondent: "Respondent",
    ClientAlignment.counter_petitioner: "Counter-Petitioner",
    ClientAlignment.counter_respondent: "Counter-Respondent",
    ClientAlignment.intervenor: "Intervenor",
    ClientAlignment.plaintiff: "Plaintiff",
    ClientAlignment.defendant: "Defendant",
    ClientAlignment.applicant: "Applicant",
    ClientAlignment.movant: "Movant",
    ClientAlignment.other: "Party",
}


class MatterType(str, Enum):
    """Broad category of legal matter — drives fee agreement templates."""
    divorce = "divorce"
    child_custody = "child_custody"
    modification = "modification"
    enforcement = "enforcement"
    cps = "cps"
    probate = "probate"
    estate_planning = "estate_planning"
    civil = "civil"
    other = "other"


class MatterStaff(BaseModel):
    """
    Association between a matter and a staff member.

    Stored in the ``matter_staff`` table. Originators carry a ``split_pct``
    that must sum to 100 across all originating records for a given matter.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    staff_id: int = Field(..., description="Foreign key to the staff table")
    role: str = Field(
        ...,
        description="Staff role on this matter: 'originating', 'billing_reviewer', 'assigned'",
    )
    split_pct: Optional[float] = Field(
        default=None,
        description="Origination credit percentage (0–100). Required when role='originating'",
    )


class MatterStaffInDB(MatterStaff):
    """Database model for matter-staff associations."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    model_config = ConfigDict(from_attributes=True)


class BillingSplit(BaseModel):
    """
    Multi-client billing split for appointed matters.

    Percentages across all splits for a given matter must sum to 100.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    client_id: int = Field(..., description="Foreign key to the clients table — the billed party")
    split_pct: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Percentage of billing responsibility assigned to this client (0–100)",
    )


class BillingSplitInDB(BillingSplit):
    """Database model for billing splits."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    model_config = ConfigDict(from_attributes=True)


class OpposingParty(BaseModel):
    """
    Opposing party on a matter, used for conflict checks.

    Stored in the ``opposing_parties`` table and queried by the conflict
    check service using trigram similarity (pg_trgm).
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    full_name: str = Field(..., description="Full name of the opposing party as it should appear in conflict searches")
    relationship: Optional[str] = Field(
        default=None,
        description="Relationship to the client, e.g. 'spouse', 'co-defendant'",
    )


class OpposingPartyInDB(OpposingParty):
    """Database model for opposing parties."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    model_config = ConfigDict(from_attributes=True)


class RateCard(BaseModel):
    """
    Per-matter rates by staff role.

    Used by ``BillingService.resolve_rate`` as the second tier of rate
    resolution:

    1. ``matter_rate_overrides`` (per-staff, per-matter override row)
    2. ``Matter.rate_card`` — *this model* — by staff role
    3. ``StaffMember.default_billing_rate``

    Stored as JSONB on the ``matters.rate_card`` column. Admin is
    intentionally absent: admins do not bill time. Per-staff overrides
    live in the ``matter_rate_overrides`` table, not here.
    """
    attorney: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Hourly rate in USD for any attorney working this matter",
    )
    paralegal: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Hourly rate in USD for any paralegal working this matter",
    )

    model_config = ConfigDict(extra="ignore")


class Matter(BaseModel):
    """
    Domain model for a legal matter.

    A matter belongs to one primary client but may have billing splits
    across multiple clients (see BillingSplit). See ``RateCard`` above for
    the structure of the ``rate_card`` field.
    """
    client_id: int = Field(..., description="Foreign key to the primary client on this matter")
    short_name: Optional[str] = Field(
        default=None,
        description="Short display name, e.g. 'SALMONS - divorce - 2026'. "
                    "Auto-generated from client last name, matter type, and year if not provided.",
    )
    matter_name: str = Field(..., description="Human-readable matter name, e.g. 'Smith v. Jones Divorce'")
    matter_type: MatterType = Field(..., description="Category of matter; drives fee agreement template selection")
    status: MatterStatus = Field(default=MatterStatus.intake, description="Current lifecycle status of the matter")
    billing_review_staff_id: Optional[int] = Field(
        default=None,
        description="Foreign key to the staff member responsible for approving bills on this matter",
    )
    rate_card: RateCard = Field(
        default_factory=RateCard,
        description="Per-role hourly rates for this matter. See RateCard.",
    )
    retainer_amount: float = Field(
        default=0.0,
        ge=0.0,
        description="Initial retainer amount in USD",
    )
    refresh_trigger_pct: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Fraction of retainer at which a replenishment is requested (e.g. 0.40 = 40%)",
    )
    is_pro_bono: bool = Field(
        default=False,
        description="When True, all TIME billing entries on this matter are automatically set to $0.00. "
                    "Expense and flat-fee entries are unaffected.",
    )
    fee_agreement_signed_date: Optional[date] = Field(
        default=None,
        description="Date the fee agreement was signed by the client",
    )
    opened_date: Optional[date] = Field(
        default=None,
        description="Date the matter was officially opened",
    )
    closed_date: Optional[date] = Field(
        default=None,
        description="Date the matter was closed",
    )
    state: str = Field(
        default="Texas",
        description="State where the matter is filed or administered",
    )
    county: str = Field(
        ...,
        description="County where the matter is filed",
    )
    court_name: Optional[str] = Field(
        default=None,
        description="Name of the court, e.g. '401st District Court'",
    )
    matter_number: Optional[str] = Field(
        default=None,
        description="Court-assigned case/matter number",
    )
    discovery_level: Optional[DiscoveryLevel] = Field(
        default=None,
        description="Texas TRCP 190 discovery level for this matter",
    )
    notes: Optional[str] = Field(default=None, description="Internal matter notes; not visible to the client")
    case_style: Optional[str] = Field(
        default=None,
        description="Formal style of the case as written on a filing, e.g. 'IN THE MATTER OF THE "
                    "MARRIAGE OF JANE DOE AND JOHN DOE'. Distinct from matter_name, which is the "
                    "internal short name — it heads every exhibit this matter produces",
    )
    client_alignment: Optional[ClientAlignment] = Field(
        default=None,
        description="Which side our client is on, in caption vocabulary. Titles every exhibit: "
                    "\"Petitioner's Financial Summary\"",
    )

    @field_validator("refresh_trigger_pct")
    @classmethod
    def validate_refresh_trigger_pct(cls, v: float) -> float:
        """Ensure the refresh trigger is expressed as a fraction, not a percentage."""
        if v > 1.0:
            raise ValueError("refresh_trigger_pct must be a fraction between 0 and 1, not a percentage")
        return v


class MatterInDB(Matter):
    """Database model — extends Matter with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)


class MatterRateOverride(BaseModel):
    """
    Per-matter billing rate override for an individual staff member.

    Stored in the ``matter_rate_overrides`` table. When a billing entry is
    committed for a matter, BillingService resolves the effective rate in
    this order:

    1. ``MatterRateOverride`` for the specific staff member on this matter
    2. ``Matter.rate_card`` lookup by role name
    3. ``StaffMember.default_billing_rate``

    Overrides are not applicable when ``Matter.is_pro_bono`` is True —
    TIME entries are billed at $0.00 regardless.
    """
    matter_id: int = Field(..., description="Foreign key to the matters table")
    staff_id: int = Field(..., description="Foreign key to the staff member whose rate is overridden")
    rate: float = Field(
        ...,
        ge=0.0,
        description="Override hourly rate in USD for this staff member on this specific matter",
    )


class MatterRateOverrideInDB(MatterRateOverride):
    """Database model — extends MatterRateOverride with DB-managed metadata."""
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Timestamp of record creation, set by the database")
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp of last update, set by the database on update",
    )
    model_config = ConfigDict(from_attributes=True)

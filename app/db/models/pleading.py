"""
app/db/models/pleading.py - Pleadings, claims, children, and opposing counsel models.

These tables populate the pleading ingestion pipeline, which extracts case
metadata, children, opposing counsel, and claims/defenses from uploaded PDFs.
"""
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from db.models.staff import FullName


# ── Enums ─────────────────────────────────────────────────────────────────────

class ChildSex(str, Enum):
    male = "male"
    female = "female"
    other = "other"


class CounselRole(str, Enum):
    lead = "lead"
    co_counsel = "co_counsel"
    local_counsel = "local_counsel"


class ClaimKind(str, Enum):
    claim = "claim"
    defense = "defense"
    affirmative_defense = "affirmative_defense"
    counterclaim = "counterclaim"


# ── Matter Children ───────────────────────────────────────────────────────────

class MatterChild(BaseModel):
    """A child of the marriage or relationship at issue in a matter."""
    matter_id: int = Field(..., description="FK to matters table")
    name: FullName = Field(..., description="Child's full legal name")
    date_of_birth: date = Field(..., description="Child's date of birth")
    sex: ChildSex = Field(..., description="Child's sex")
    needs_support_after_majority: bool = Field(
        default=False,
        description="Will this child need support past the age of majority (disabled, special needs, etc.)",
    )


class MatterChildInDB(MatterChild):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


# ── Opposing Counsel ──────────────────────────────────────────────────────────

class OpposingCounsel(BaseModel):
    """
    An opposing attorney. Deduplicated by (bar_state, bar_number) so that
    updates to an attorney's firm, email, or phone propagate across all
    matters they appear on.
    """
    name: FullName = Field(..., description="Attorney's full legal name")
    firm_name: Optional[str] = Field(default=None, description="Law firm name")
    street_address: Optional[str] = Field(default=None)
    street_address_2: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None, description="Mailing address state")
    postal_code: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    cell_phone: Optional[str] = Field(default=None)
    telephone: Optional[str] = Field(default=None)
    fax: Optional[str] = Field(default=None)
    bar_state: str = Field(..., description="State that issued the bar card, e.g. 'TX'")
    bar_number: str = Field(..., description="Bar card number")
    email_ccs: list[str] = Field(
        default_factory=list,
        description="Additional email addresses to CC on correspondence",
    )


class OpposingCounselInDB(OpposingCounsel):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


# ── Matter ↔ Opposing Counsel intersection ────────────────────────────────────

class MatterOpposingCounsel(BaseModel):
    """Association of an opposing counsel with a specific matter and party."""
    matter_id: int = Field(..., description="FK to matters table")
    opposing_counsel_id: int = Field(..., description="FK to opposing_counsel table")
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="FK to opposing_parties table — which opposing party this counsel represents",
    )
    role: CounselRole = Field(default=CounselRole.lead, description="Counsel's role on this matter")
    started_date: Optional[date] = Field(default=None, description="When counsel began representing on this matter")
    ended_date: Optional[date] = Field(default=None, description="When counsel withdrew, if applicable")


class MatterOpposingCounselInDB(MatterOpposingCounsel):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


# ── Matter Pleadings ──────────────────────────────────────────────────────────

class MatterPleading(BaseModel):
    """
    A single pleading filed in a matter.

    A pleading is "live" until it is superseded by an amendment. Supplements
    add to (but do not supersede) the live pleading they are based on.
    """
    matter_id: int = Field(..., description="FK to matters table")
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="FK to opposing_parties — whose pleading is this. Null means our client's.",
    )
    title: str = Field(..., description="Title of the pleading, e.g. 'Original Petition for Divorce'")
    filed_date: Optional[date] = Field(default=None, description="Date the pleading was filed with the court")
    served_date: Optional[date] = Field(default=None, description="Date the pleading was served on our client")
    amends_pleading_id: Optional[int] = Field(
        default=None,
        description="Self-reference to a prior pleading that this one amends and supersedes",
    )
    is_supplement: bool = Field(
        default=False,
        description="True for supplements (which add to but do not supersede prior pleadings)",
    )
    storage_path: Optional[str] = Field(default=None, description="Supabase Storage path to the PDF")
    raw_text: Optional[str] = Field(default=None, description="Extracted text, for re-processing later")
    ingested_by_staff_id: int = Field(..., description="FK to staff member who ingested this pleading")


class MatterPleadingInDB(MatterPleading):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


# ── Matter Claims ─────────────────────────────────────────────────────────────

class MatterClaim(BaseModel):
    """
    A claim, defense, affirmative defense, or counterclaim extracted from
    a pleading.

    Used to provide context for discovery drafting, objection/response
    generation, witness examination outlines, and strategic analysis.
    """
    matter_pleading_id: int = Field(..., description="FK to the pleading this claim was extracted from")
    matter_id: int = Field(..., description="FK to matters (denormalized for fast matter-level queries)")
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="FK to opposing_parties — whose claim is this. Null means our client's.",
    )
    kind: ClaimKind = Field(..., description="Type of legal position")
    label: str = Field(..., description="Short label, e.g. 'Fault: adultery'")
    narrative: str = Field(..., description="Full text of the claim as stated in the pleading")
    statute_rule_cited: Optional[str] = Field(
        default=None,
        description="Statute or rule cited in support, e.g. 'TFC 6.003'",
    )


class MatterClaimInDB(MatterClaim):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)

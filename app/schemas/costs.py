"""
app/schemas/costs.py - Prices and spend, as the Costs page sees them.

Money crosses the wire as a STRING (CLAUDE.md §11), so exact figures from
Postgres ``numeric`` survive to the screen. A price keeps all six decimals --
some models cost less than a cent per million tokens, and a price rounded to
cents would be zero.
"""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from db.models.service_rate import RateMeter


class CostGroup(str, Enum):
    """How a spend report is broken down. Must match llm_cost_report's list."""

    # One firm today, and the column has been on every ledger row since 039
    # because attribution cannot be added afterwards. It is also the grouping
    # that says what the others mean: "spend by matter" across two firms is a
    # list nobody can act on without knowing whose matters they are (043).
    firm = "firm"
    vendor_model = "vendor_model"
    profile = "profile"
    matter = "matter"
    staff = "staff"
    source = "source"


class ServiceRateResponse(BaseModel):
    """A price, with whether it is the one in force now."""

    id: int = Field(..., description="Primary key")
    provider: str = Field(..., description="Vendor, as recorded on llm_calls")
    model: str = Field(..., description="Model, as recorded on llm_calls")
    meter: RateMeter = Field(..., description="Which usage this prices")
    unit_count: int = Field(..., description="Price is per this many units")
    price: str = Field(..., description="Exact decimal string, six places")
    currency: str = Field(..., description="Always USD")
    effective_from: datetime = Field(..., description="Inclusive start")
    effective_to: Optional[datetime] = Field(default=None, description="Exclusive end; null while in force")
    in_force: bool = Field(..., description="Whether this price applies to a call made now")
    source_url: Optional[str] = Field(default=None, description="Where the figure was copied from")
    note: Optional[str] = Field(default=None, description="Why this price")
    created_at: datetime = Field(..., description="When it was entered")


class SetServiceRateRequest(BaseModel):
    """A new price from a date. Closes the price in force at that date."""

    provider: str = Field(..., min_length=1, description="Vendor, exactly as llm_calls records it")
    model: str = Field(..., min_length=1, description="Model, exactly as llm_calls records it")
    meter: RateMeter = Field(..., description="Which usage this prices")
    price: Decimal = Field(..., ge=0, max_digits=14, decimal_places=6,
                           description="Price per unit_count. Sent as a string to keep it exact")
    unit_count: int = Field(default=1_000_000, gt=0, description="Price is per this many units")
    effective_from: datetime = Field(..., description="From when; a price may be entered late")
    source_url: Optional[str] = Field(default=None, description="Vendor pricing page")
    note: Optional[str] = Field(default=None, description="Why this price")

    @field_validator("provider", "model")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        # The price joins to llm_calls on these EXACTLY. A trailing space from a
        # paste would leave every call unpriced with nothing visibly wrong.
        return value.strip()


class CorrectServiceRateRequest(BaseModel):
    """
    Fix a typo in a price already entered. PATCH semantics.

    Dates are deliberately not correctable here: moving a period is "a new price
    from a date", which closes and opens atomically. Read with ``exclude_unset``
    so a field can be cleared (§18).
    """

    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=14, decimal_places=6,
                                     description="Corrected price")
    source_url: Optional[str] = Field(default=None, description="Corrected source")
    note: Optional[str] = Field(default=None, description="Corrected note")


class CostReportRow(BaseModel):
    """Spend for one month and one group."""

    month: date = Field(..., description="First day of the UTC month")
    group_key: str = Field(..., description="Stable id of the group; '' for none")
    group_label: str = Field(..., description="What a person reads")
    calls: int = Field(..., description="Every recorded attempt")
    failed_calls: int = Field(..., description="Attempts that failed over or errored")
    no_usage_calls: int = Field(..., description="Attempts the vendor reported no usage for")
    unpriced_calls: int = Field(..., description="Attempts whose tokens meet no price")
    input_uncached_tokens: Optional[int] = Field(default=None, description="Full-rate input")
    input_cache_read_tokens: Optional[int] = Field(default=None, description="Cache reads")
    input_cache_write_tokens: Optional[int] = Field(default=None, description="Cache writes")
    output_tokens: Optional[int] = Field(default=None, description="Output, reasoning included")
    priced_cost: str = Field(..., description="Cost of the PRICED calls, to the cent")
    priced_cost_precise: str = Field(..., description="The same, to six places")


class CostReportResponse(BaseModel):
    """A spend report, with what it does not include said out loud."""

    date_from: datetime = Field(..., description="Inclusive, UTC")
    date_to: datetime = Field(..., description="Exclusive, UTC")
    group: CostGroup = Field(..., description="How rows are broken down")
    rows: list[CostReportRow] = Field(default_factory=list, description="Newest month first")
    total_calls: int = Field(..., description="All attempts in the range")
    total_unpriced_calls: int = Field(..., description="Attempts with no price in force")
    total_no_usage_calls: int = Field(..., description="Attempts with no usage reported")
    total_priced_cost: str = Field(..., description="Sum of priced cost, to the cent")
    total_priced_cost_precise: str = Field(..., description="Sum of priced cost, six places")
    complete: bool = Field(
        ..., description="True only when no call is unpriced. Otherwise the total is a FLOOR")


class UnpricedMeterResponse(BaseModel):
    """A model meter that carried tokens with no price. The pricing to-do list."""

    vendor: str = Field(..., description="Vendor to price")
    model: str = Field(
        ..., description="Model to price: the one the vendor says SERVED the calls (042), "
                         "or the requested model where no served model was reported")
    requested_models: list[str] = Field(
        default_factory=list,
        description="Models the catalog asked for on these calls. Differs from `model` when "
                    "the vendor answered under a versioned name")
    meter: RateMeter = Field(..., description="Meter to price")
    calls: int = Field(..., description="Calls affected")
    tokens: int = Field(..., description="Tokens affected")
    first_seen: datetime = Field(..., description="Earliest affected call; the price must start by here")
    last_seen: datetime = Field(..., description="Latest affected call")

"""
app/schemas/fis.py - Request and response schemas for the Financial Information
Statement.

Money crosses the wire as a string here, as everywhere else in this system, so
exact cents survive Postgres ``numeric`` and the browser never parses a figure
it is about to print onto a sworn document.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from db.models.financial import FisRecurrence


class FisRequest(BaseModel):
    """
    What to average, and over which whole months.

    The window is a first and a last **month**, not a pair of dates. A
    part-month makes "average monthly" indefensible — you cannot divide by
    three-and-a-bit and call the result a monthly figure — so the caller names
    the months and the server fills in the days.
    """
    account_ids: Optional[list[int]] = Field(
        default=None,
        description="Accounts to include; omit for every account on the matter. This is what "
                    "decides whose statement it is",
    )
    start_year: int = Field(..., ge=1900, le=2200, description="First month's year")
    start_month: int = Field(..., ge=1, le=12, description="First month, 1-12")
    end_year: int = Field(..., ge=1900, le=2200, description="Last month's year")
    end_month: int = Field(..., ge=1, le=12, description="Last month, 1-12, inclusive")
    client_id: Optional[int] = Field(
        default=None,
        description="Our client, when the statement is theirs. Selects whose payment "
                    "schedules apply — a schedule follows the person, not the matter",
    )
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="The other side, when the statement is theirs",
    )


class FisLine(BaseModel):
    """One category's row on the statement."""
    category_id: int
    parent_id: Optional[int] = None
    label: str
    depth: int = Field(..., description="0 for a top-level heading; the form is its indentation")
    monthly: Decimal = Field(..., description="The average monthly figure, signed")
    window_total: Decimal = Field(..., description="Everything in the window, before averaging")
    trailing_year_total: Decimal = Field(
        ...,
        description="Everything in the twelve months ending with the window — what a "
                    "quarterly or annual line is computed from",
    )
    transaction_count: int = Field(..., description="Lines in the window, for the drill-down")
    basis: str = Field(
        ...,
        description="window | trailing_year | stated — how this figure was reached. A reader "
                    "who cannot tell a derived figure from a supplied one cannot check either",
    )
    recurrence: Optional[FisRecurrence] = None
    legend: Optional[str] = Field(
        default=None,
        description="'paid annually', 'as incurred' — printed beside the line so the witness "
                    "explains it before opposing counsel asks. Absent on monthly lines",
    )
    note: Optional[str] = None
    empty: bool = Field(
        ...,
        description="True when a compressed statement drops this row: no figure of its own and "
                    "nothing beneath it survives",
    )


class FisWindow(BaseModel):
    start: date
    end: date
    months: int = Field(..., description="The denominator, and therefore a claim about coverage")
    trailing_start: date = Field(
        ...,
        description="Start of the twelve months the sub-monthly lines are computed from. It "
                    "reaches before the window, which the exhibit discloses",
    )


class FisCoverageAccount(BaseModel):
    account_id: int
    label: str
    months_in_window: int
    months_held: int
    missing_months: list[str] = Field(
        default_factory=list, description="YYYY-MM with no statement covering them",
    )


class FisCoverage(BaseModel):
    """
    Whether the production actually covers the window.

    The denominator asserts it does. If it does not, every figure is understated
    by a proportion invisible on the page.
    """
    complete: bool
    accounts: list[FisCoverageAccount] = Field(default_factory=list[FisCoverageAccount])


class FisExcludedCategory(BaseModel):
    """Money that moved without being income or expense."""
    category_id: int
    label: str
    total: Decimal
    transaction_count: int


class FisUncategorized(BaseModel):
    """Money nobody filed. It appears in no line, so it is reported on its own."""
    count: int
    total: Decimal
    monthly: Decimal


class FisResponse(BaseModel):
    window: FisWindow
    accounts: list[str] = Field(default_factory=list[str])
    lines: list[FisLine] = Field(default_factory=list[FisLine])
    net_monthly: Decimal
    uncategorized: FisUncategorized
    excluded: list[FisExcludedCategory] = Field(default_factory=list[FisExcludedCategory])
    coverage: FisCoverage
    warnings: list[str] = Field(
        default_factory=list[str],
        description="Everything that would make a figure below wrong or incomplete",
    )


class FisExportRequest(FisRequest):
    """
    The same selection, plus how to render it.

    Inherits the whole request so the exported statement is provably the one on
    screen -- a divergence between them would be invisible and would surface as
    an exhibit nobody could reproduce.
    """
    format: str = Field(
        default="csv",
        pattern="^(csv|md|docx|pdf)$",
        description="csv is the clean extraction; md, docx and pdf are full exhibits "
                    "carrying the caption and the verification notice",
    )
    exhibit_name: str = Field(
        default="Financial Information Statement",
        max_length=120,
        description="Titles the exhibit and names the file",
    )
    compressed: bool = Field(
        default=True,
        description="Drop lines with no amount. The condensed form is what goes to "
                    "mediation; the full form is what a court expects, because a blank "
                    "line on the form is itself an answer",
    )


class FisScheduleRequest(FisRequest):
    """The same selection, optionally narrowed to the categories in dispute."""
    category_ids: Optional[list[int]] = Field(
        default=None,
        description="Restrict to these categories, for when one line is being challenged. "
                    "Omit for the whole schedule",
    )


class FisScheduleExportRequest(FisScheduleRequest):
    format: str = Field(default="csv", pattern="^(csv|md|docx|pdf)$")
    exhibit_name: str = Field(default="Schedule of Transactions by Category", max_length=120)


class FisScheduleTransaction(BaseModel):
    """One transaction, with everything needed to find it in the production."""
    id: int
    date: Optional[date]
    description: str
    amount: Decimal
    check_number: Optional[str]
    account: str
    bates_number: Optional[str]
    page: Optional[int]
    document: Optional[str] = Field(
        default=None,
        description="The uploaded filename. Storage renames every upload to a job id, so "
                    "without this the Bates number is the only handle on the source",
    )
    statement_id: int
    category_source: Optional[str] = Field(
        default=None,
        description="human | rule | similarity. None means never categorized",
    )
    category_rule_id: Optional[int] = None
    reviewed: bool = Field(
        default=False,
        description="Whether a person has confirmed an automatic assignment. The review "
                    "queue is everything filed by machine where this is false",
    )


class FisScheduleGroup(BaseModel):
    category_id: Optional[int] = Field(default=None, description="None for unfiled money")
    label: str
    depth: int
    basis: str
    recurrence: Optional[FisRecurrence] = None
    legend: Optional[str] = None
    monthly: Decimal = Field(..., description="Taken from the statement, never recomputed")
    total: Decimal
    span: str = Field(..., description="window | trailing_year — which months these cover")
    span_start: date
    span_end: date
    derivation: str = Field(
        ...,
        description="How these transactions become the figure on the statement. The sentence "
                    "a witness reads out instead of guessing",
    )
    transactions: list[FisScheduleTransaction] = Field(
        default_factory=list[FisScheduleTransaction])


class FisScheduleResponse(BaseModel):
    window: FisWindow
    accounts: list[str] = Field(default_factory=list[str])
    groups: list[FisScheduleGroup] = Field(default_factory=list[FisScheduleGroup])
    warnings: list[str] = Field(default_factory=list[str])


class FisSettingRequest(BaseModel):
    """
    Set one person's payment schedule for one category.

    With neither party named this writes the firm-wide default, which is how a
    chart gets its sensible starting point — property taxes annual, mortgage
    monthly — without anyone setting it case by case.
    """
    category_id: int
    recurrence: FisRecurrence = FisRecurrence.monthly
    stated_annual_amount: Optional[Decimal] = Field(
        default=None,
        description="The attorney's own annual figure, which wins over anything derived. "
                    "SIGNED: negative for an expense. It cannot take its sign from the "
                    "transactions, because the reason to state it is that they show none",
    )
    note: Optional[str] = Field(default=None, max_length=200)
    client_id: Optional[int] = None
    opposing_party_id: Optional[int] = None


class FisSettingResponse(BaseModel):
    id: int
    client_id: Optional[int]
    opposing_party_id: Optional[int]
    category_id: int
    recurrence: FisRecurrence
    stated_annual_amount: Optional[Decimal]
    note: Optional[str]
    is_default: bool = Field(
        ...,
        description="True when this row is the firm-wide default rather than this person's. "
                    "An editor must be able to tell an inherited value from an owned one, or "
                    "saving would silently pin a default that should have kept moving",
    )

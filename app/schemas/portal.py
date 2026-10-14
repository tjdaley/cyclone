"""
app/schemas/portal.py - What the client portal returns.

Deliberately NOT the staff schemas with a filter on top. Two audiences read the
same facts differently, and the staff wording is written for a paralegal:
"Extraction very likely stopped early — re-ingest before relying on it" tells a
client their bank statement is broken. A separate shape makes the difference
explicit instead of leaving it to whoever edits the staff schema next.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from db.models.financial import AccountType


class PortalMatter(BaseModel):
    """One of the client's matters, as the portal names it."""

    id: int = Field(..., description="Matter id")
    matter_name: str = Field(..., description="How the firm refers to the case")
    matter_number: Optional[str] = Field(default=None, description="Cause number, when there is one")
    tagging_enabled: bool = Field(
        ...,
        description="Whether the client may tag on this matter now. Off during hearing prep, and "
                    "the portal says so rather than hiding the controls",
    )


class PortalAccount(BaseModel):
    """An account on the matter. Read-only: the client cannot change any of it."""

    id: int
    institution: str
    account_type: AccountType
    account_number_last4: Optional[str] = None
    name_on_account: Optional[str] = None
    is_closed: bool = False
    statements: int = Field(default=0, description="How many statements are held for it")


class PortalStatement(BaseModel):
    """
    A statement held for an account.

    No flags, no extraction provenance, no review status: those are staff
    signals. ``complete`` is the one fact worth a client's attention, and it is
    worded as a question for the firm rather than a fault.
    """

    id: int
    period_start: date
    period_end: date
    beginning_balance: Optional[Decimal] = None
    ending_balance: Optional[Decimal] = None
    transactions: int = Field(default=0, description="Lines read from it")
    complete: bool = Field(
        ...,
        description="True when the statement's figures tie out. False means the firm is still "
                    "checking it — never phrased as the client's problem",
    )


class PortalTag(BaseModel):
    """A tag the firm has published to clients."""

    id: int
    label: str
    description: Optional[str] = None
    color: Optional[str] = None


class PortalTransaction(BaseModel):
    """One line, with the client's own tagging state."""

    id: int
    statement_id: int
    financial_account_id: int
    transaction_date: Optional[date] = None
    description: str
    counterparty: Optional[str] = None
    amount: Decimal
    check_number: Optional[str] = None
    physical_page_number: Optional[int] = None
    bates_number: Optional[str] = None
    institution: Optional[str] = None
    account_last4: Optional[str] = None
    tag_ids: list[int] = Field(default_factory=list, description="Live tags on this line")
    cleared_tag_ids: list[int] = Field(
        default_factory=list,
        description="Tags the firm reviewed and removed. Shown greyed and not re-appliable — "
                    "hiding them silently means the client re-tags, is refused, and reports a bug",
    )


class PortalTransactionPage(BaseModel):
    total: int = Field(..., description="Every matching line, not just this page")
    items: list[PortalTransaction]


class PortalTagRequest(BaseModel):
    """Apply or remove one tag on one line."""

    tag_id: int = Field(..., description="Must be a tag published to clients on this matter")


class PortalTagResult(BaseModel):
    outcome: str = Field(..., description="applied | already | removed | absent")
    tag_ids: list[int] = Field(default_factory=list, description="The line's live tags afterwards")
    cleared_tag_ids: list[int] = Field(default_factory=list, description="And what stays cleared")


class ClientTagReview(BaseModel):
    """A client's tag, as the staff review screen sees it."""

    link_id: int
    transaction_id: int
    tag_id: int
    tag_label: str
    matter_id: int
    transaction_date: Optional[date] = None
    description: str
    amount: Decimal
    institution: Optional[str] = None
    account_last4: Optional[str] = None
    tagged_at: datetime
    cleared_at: Optional[datetime] = None

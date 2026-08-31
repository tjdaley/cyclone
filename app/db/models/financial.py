"""
app/db/models/financial.py - Domain and database models for account statements.

Three levels, mirroring how the documents themselves arrive:

    FinancialAccount            an account on a matter (bank, brokerage, card)
    FinancialAccountStatement   one statement period for that account
    FinancialAccountTransaction one line on that statement

Amounts are signed by how they move the balance the institution prints — a
deposit and a card purchase are both positive, a withdrawal and a card payment
both negative — so one formula reconciles every account type::

    beginning_balance + sum(amount) == ending_balance
"""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AccountType(str, Enum):
    """Values must match the financial_accounts CHECK constraint."""
    checking = "checking"
    savings = "savings"
    brokerage = "brokerage"
    credit_card = "credit_card"
    retirement = "retirement"
    hsa = "hsa"
    loan = "loan"
    other = "other"


class PropertyCharacter(str, Enum):
    """
    Characterization for the inventory.

    Left unset until decided: character is argued, not extracted, and often
    stays open until trial.
    """
    community = "community"
    separate_petitioner = "separate_petitioner"
    separate_respondent = "separate_respondent"
    mixed = "mixed"
    disputed = "disputed"


class AccountOwnership(str, Enum):
    """
    Who holds an account.

    Ownership used to be inferred from ``opposing_party_id`` — null meant our
    client's, a value meant theirs. That encoding has no way to say *joint*,
    and joint is not a detail: it is the difference between an asset one side
    keeps and an asset the court divides.

    ``opposing_party_id`` keeps its job of naming *which* other party, and on a
    joint account it names the co-holder rather than the sole owner.
    """
    client_sole = "client_sole"
    opposing_sole = "opposing_sole"
    joint = "joint"              # Our client and the named opposing party
    third_party = "third_party"  # A business, a trust, a relative
    unknown = "unknown"          # Not yet determined — the honest default


class StatementReviewStatus(str, Enum):
    """
    Where a statement sits in the exceptions workflow.

    A statement that reconciles and carries no blocking flag is
    ``auto_accepted`` without anyone looking at it; everything else waits in
    ``needs_review``. That is what makes a document dump of several hundred
    statements tractable.
    """
    auto_accepted = "auto_accepted"
    needs_review = "needs_review"
    accepted = "accepted"      # An attorney looked at an exception and kept it
    rejected = "rejected"      # Bad extraction, discarded so the PDF can be re-run


class DateProvenance(str, Enum):
    """Whether a date was printed on the statement or derived from context."""
    printed = "printed"
    derived = "derived"
    unknown = "unknown"


class FinancialAccount(BaseModel):
    """An account belonging to a matter."""
    matter_id: int = Field(..., description="FK to matters")
    institution: str = Field(..., description="Bank, brokerage, or issuer name as printed")
    account_type: AccountType = Field(..., description="What kind of account this is")
    account_number_last4: Optional[str] = Field(
        default=None,
        description="Last four digits — the dedup key with institution. Never store the full number",
    )
    account_number_masked: Optional[str] = Field(
        default=None,
        description="Masked form exactly as the statement prints it, e.g. 'ending in 4357'",
    )
    name_on_account: Optional[str] = Field(default=None, description="Names on the account, as printed")
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="Which other party is involved: the sole owner when ownership is "
                    "'opposing_sole', the co-holder when it is 'joint'",
    )
    ownership: AccountOwnership = Field(
        default=AccountOwnership.unknown,
        description="Who holds the account. Defaults to unknown because extraction never "
                    "determines it — this is a characterization, and it drives division",
    )
    property_character: Optional[PropertyCharacter] = Field(
        default=None,
        description="Community/separate characterization for the inventory; unset until decided",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="What the account is for, in the client's own words, e.g. 'IRS money'",
    )
    notes: Optional[str] = Field(default=None)
    antecedent_account_id: Optional[int] = Field(
        default=None,
        description="The account this one succeeds — a reissued card, a bank migration, a rollover. "
                    "Points backwards so a new number can be linked the day it appears, before "
                    "anyone knows whether it will itself be replaced",
    )
    is_closed: bool = Field(default=False, description="True once the account is known to be closed")


class FinancialAccountInDB(FinancialAccount):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class FinancialAccountStatement(BaseModel):
    """One statement period for one account."""
    financial_account_id: int = Field(..., description="FK to financial_accounts")
    matter_id: int = Field(
        ...,
        description="Denormalized from the account; a composite FK keeps it equal to the parent's",
    )
    period_start: date = Field(..., description="First day of the statement period")
    period_end: date = Field(..., description="Last day of the statement period")
    beginning_balance: Optional[Decimal] = Field(default=None, description="Balance the statement opens with")
    ending_balance: Optional[Decimal] = Field(default=None, description="Balance the statement prints as its close")
    computed_ending_balance: Optional[Decimal] = Field(
        default=None,
        description="beginning_balance + sum of transaction amounts, computed on commit",
    )
    reconciled: bool = Field(
        default=False,
        description="True when the computed close matches the printed close to the cent",
    )
    reconciliation_delta: Optional[Decimal] = Field(
        default=None,
        description="printed minus computed. Recorded, never corrected by inventing a row",
    )
    printed_totals: dict[str, Any] = Field(
        default_factory=dict,
        description="Totals the statement prints (payments, purchases, fees, interest) — a second check",
    )
    flags: list[dict[str, Any]] = Field(
        default_factory=list[dict[str, Any]],
        description="Statement-level findings: NO_ACCOUNT_MATCH, DUPLICATE_PERIOD, UNRECONCILED",
    )
    review_status: StatementReviewStatus = Field(default=StatementReviewStatus.needs_review)
    storage_path: Optional[str] = Field(default=None, description="Supabase Storage path to the source PDF")
    raw_text: Optional[str] = Field(default=None, description="Extracted text, kept for re-extraction")
    extraction: dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance: model, profile, job id, source file, page count",
    )
    source_job_id: Optional[str] = Field(default=None, description="The ingest job that produced this")
    ingested_by_staff_id: int = Field(..., description="FK to the staff member who ingested it")


class FinancialAccountStatementInDB(FinancialAccountStatement):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class FinancialAccountTransaction(BaseModel):
    """One line on a statement."""
    statement_id: int = Field(..., description="FK to financial_account_statements")
    financial_account_id: int = Field(
        ...,
        description="Denormalized from the statement; a composite FK keeps it equal to the parent's",
    )
    line_no: int = Field(..., description="Order on the statement, 1-based — preserves the printed sequence")
    transaction_date: Optional[date] = Field(default=None, description="Date of the transaction")
    posted_date: Optional[date] = Field(default=None, description="Date it posted, when printed separately")
    date_provenance: DateProvenance = Field(
        default=DateProvenance.printed,
        description="'derived' when the year came from the statement period rather than the line",
    )
    description: str = Field(..., description="Description as printed, joined to one line")
    description_lines: list[str] = Field(
        default_factory=list,
        description="The raw printed lines, so an exhibit can quote the document verbatim",
    )
    counterparty: Optional[str] = Field(
        default=None,
        description="Normalized payee, for grouping the same merchant across an account's history",
    )
    location: Optional[str] = Field(default=None, description="City/state, when the line carries one")
    amount: Decimal = Field(
        ...,
        description="Signed by its effect on the printed balance: deposits and card purchases "
                    "positive, withdrawals and card payments negative",
    )
    running_balance: Optional[Decimal] = Field(
        default=None,
        description="Balance after this line, when the statement prints one",
    )
    category: Optional[str] = Field(
        default=None,
        description="Free-text guess from extraction — a hint for the person categorizing, never authoritative",
    )
    category_id: Optional[int] = Field(
        default=None,
        description="FK to transaction_categories — the authoritative bucket, set by a human",
    )
    physical_page_number: Optional[int] = Field(
        default=None,
        description="1-based page of the source PDF this line was printed on",
    )
    bates_number: Optional[str] = Field(
        default=None,
        description="Bates number stamped on that page, exactly as printed; None when unstamped",
    )
    check_number: Optional[str] = Field(
        default=None,
        description="Check number this was drawn on, as printed. A check is the one debit that "
                    "does not say where the money went, so the number is what a discovery request "
                    "asks about. Text: leading zeros are kept and it is never arithmetic",
    )
    flags: list[dict[str, Any]] = Field(
        default_factory=list[dict[str, Any]],
        description="Per-line findings: YEAR_INFERRED, LOCATION_INFERRED, SIGN_ASSUMED",
    )
    deleted_at: Optional[datetime] = Field(
        default=None,
        description="Set when a person drops the line from the statement. Hidden everywhere by "
                    "default and excluded from reconciliation, but kept: dropping a line asserts "
                    "it is not printed on the document, and that assertion reaches an exhibit",
    )
    deleted_by_staff_id: Optional[int] = Field(default=None, description="Who dropped it")
    deletion_reason: Optional[str] = Field(default=None, description="Why, in their words")


class FinancialAccountTransactionInDB(FinancialAccountTransaction):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    model_config = ConfigDict(from_attributes=True)


# ── Classification ───────────────────────────────────────────────────────────
#
# Two axes, deliberately separate.
#
# A *category* is one bucket per transaction, drawn from a firm-wide hierarchy.
# It drives the Financial Information Statement — the personal income statement
# filed for a temporary orders hearing — where every line must land in exactly
# one place or the totals double-count.
#
# A *tag* is many-to-many and drives everything else: the Rule 1006 summaries
# behind waste, constructive fraud, and reimbursement claims. One line can be
# evidence in several exhibits at once, which is why it cannot be a column.


class TransactionCategory(BaseModel):
    """
    One node of the firm-wide category tree.

    Firm-wide rather than per-matter on purpose: an FIS is only comparable
    across cases when every case buckets to the same chart of accounts.
    """
    description: str = Field(..., description="Display name, unique among its siblings")
    parent_id: Optional[int] = Field(
        default=None,
        description="Parent category; None for a top-level heading. Nests to arbitrary depth",
    )
    display_order: int = Field(
        default=0,
        description="Sort key across the whole tree, not within the parent; seeded with gaps of five",
    )
    include_in_fis: bool = Field(
        default=True,
        description="Whether this bucket appears on the Financial Information Statement. "
                    "False for money that moves without being income or expense — a stock "
                    "split, a transfer between the parties' own accounts",
    )
    is_active: bool = Field(
        default=True,
        description="Retire a category without disturbing the transactions already filed under it",
    )


class TransactionCategoryInDB(TransactionCategory):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class TransactionTag(BaseModel):
    """
    A label that can be applied to any number of transactions.

    Two layers in one table: ``matter_id`` of None is a firm-wide tag offered on
    every matter ("Waste Claim"); a value scopes the tag to one case ("Waste:
    Sister's Wedding"). One table means a line's tags are one join, not a union.
    """
    matter_id: Optional[int] = Field(
        default=None,
        description="None for a firm-wide tag; a matter id scopes it to that case",
    )
    label: str = Field(..., description="What the tag says on the chip")
    description: Optional[str] = Field(default=None, description="What it means, for whoever tags next")
    color: Optional[str] = Field(default=None, description="Presentation token, e.g. 'amber'")
    display_order: int = Field(default=0, description="Sort key within its layer")
    is_active: bool = Field(default=True, description="Retire without removing it from tagged lines")


class TransactionTagInDB(TransactionTag):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class TransactionTagLink(BaseModel):
    """
    One tag applied to one transaction.

    ``tagged_by_staff_id`` is not bookkeeping: tagging is an attorney judgment
    that gets cross-examined, so the record says who made it.
    """
    transaction_id: int = Field(..., description="FK to financial_account_transactions")
    tag_id: int = Field(..., description="FK to transaction_tags")
    tagged_by_staff_id: int = Field(..., description="Staff member who applied the tag")


class TransactionTagLinkInDB(TransactionTagLink):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    model_config = ConfigDict(from_attributes=True)

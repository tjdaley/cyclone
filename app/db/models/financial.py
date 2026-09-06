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


class CategorySource(str, Enum):
    """
    Who filed a transaction under its category.

    None of these is a quality judgment; they are provenance. A rule assignment
    is not worse than a human one, it is *answerable differently* — "the
    description contains WALMART" rather than "a paralegal read it" — and a
    reviewer needs to know which answer they are relying on.
    """
    human = "human"
    rule = "rule"
    similarity = "similarity"


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
    category_source: Optional[CategorySource] = Field(
        default=None,
        description="Who filed this line: human, rule, or similarity. None means it has never "
                    "been categorized — which is not the same as a person deciding it belongs "
                    "nowhere, and that case is a human source with a null category_id",
    )
    category_rule_id: Optional[int] = Field(
        default=None,
        description="The rule that filed it. Not a foreign key on purpose: the trail must "
                    "outlive the rule, including when the rule is deleted for being wrong",
    )
    category_set_by_staff_id: Optional[int] = Field(default=None)
    category_set_at: Optional[datetime] = Field(default=None)
    category_reviewed_by_staff_id: Optional[int] = Field(default=None)
    category_reviewed_at: Optional[datetime] = Field(
        default=None,
        description="When a person confirmed an automatic assignment. Reviewed-and-correct has "
                    "to be distinguishable from never-looked-at or the queue never empties",
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
    is_liability: bool = Field(
        default=False,
        description="Whether money filed here was paid to a creditor — a card issuer, a "
                    "lender, a mortgage servicer. Read by the creditor-discovery scan: a "
                    "payment filed here names an account somebody may not have produced. "
                    "Distinct from include_in_fis, which asks whether the line belongs on "
                    "the sworn statement at all",
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


# ── The Financial Information Statement ──────────────────────────────────────
#
# The FIS reports an average MONTHLY figure over a window of whole months. For
# anything paid monthly that is the window total over the window's length. For
# anything paid less often it is wrong, and wrong in a way that moves: $3,600 of
# property tax paid once in January reads as $1,800/month over a Jan–Feb window
# and $1,200/month over Jan–Mar. Same facts, different sworn figure every time
# the report is re-run.


class FisRecurrence(str, Enum):
    """
    How often money moves in a category, for one person.

    Splits the averaging in two. At monthly or more often, the window total
    already spans the right number of payments and is divided by the window's
    months. Less often than monthly, the payment buys coverage that extends past
    the window, so the figure comes from the trailing twelve months over twelve.

    ``irregular`` computes like monthly and exists for the legend: genuinely
    unscheduled spending — medical, repairs — is honestly described as incurred
    rather than as a schedule it does not keep.
    """
    weekly = "weekly"
    biweekly = "biweekly"
    semimonthly = "semimonthly"
    monthly = "monthly"
    quarterly = "quarterly"
    semiannual = "semiannual"
    annual = "annual"
    irregular = "irregular"

    @property
    def periods_per_year(self) -> Optional[int]:
        """How many times a year money moves. None for ``irregular``."""
        return _PERIODS_PER_YEAR[self]

    @property
    def is_sub_monthly(self) -> bool:
        """True when a payment covers more than the month it falls in."""
        periods = self.periods_per_year
        return periods is not None and periods < 12

    @property
    def legend(self) -> str:
        """How the FIS describes the line, so a witness can explain it."""
        return _RECURRENCE_LEGEND[self]


_PERIODS_PER_YEAR: dict[FisRecurrence, Optional[int]] = {
    FisRecurrence.weekly: 52,
    FisRecurrence.biweekly: 26,
    FisRecurrence.semimonthly: 24,
    FisRecurrence.monthly: 12,
    FisRecurrence.quarterly: 4,
    FisRecurrence.semiannual: 2,
    FisRecurrence.annual: 1,
    FisRecurrence.irregular: None,
}

# Spelled out rather than derived from the enum name: these are printed on a
# document a witness reads aloud, and "paid semiannually" is not how anyone
# says it.
_RECURRENCE_LEGEND: dict[FisRecurrence, str] = {
    FisRecurrence.weekly: "paid weekly",
    FisRecurrence.biweekly: "paid every two weeks",
    FisRecurrence.semimonthly: "paid twice monthly",
    FisRecurrence.monthly: "paid monthly",
    FisRecurrence.quarterly: "paid quarterly",
    FisRecurrence.semiannual: "paid twice yearly",
    FisRecurrence.annual: "paid annually",
    FisRecurrence.irregular: "as incurred",
}


class FisCategorySetting(BaseModel):
    """
    One person's payment schedule for one category.

    **Scoped to the person, not the matter.** A schedule is a fact about
    somebody's finances, not about a lawsuit — the same client may have matters
    in several counties from successive marriages and pays property taxes on the
    same schedule in all of them. Two layers, as with tags: neither party set is
    the firm-wide default, and a row naming a party overrides it.
    """
    client_id: Optional[int] = Field(
        default=None,
        description="Our client this applies to. None with opposing_party_id None means "
                    "the firm-wide default",
    )
    opposing_party_id: Optional[int] = Field(
        default=None,
        description="The other side this applies to. Matter-scoped, because opposing_parties "
                    "is — so these stay with the matter while a client's follow the client",
    )
    category_id: int = Field(..., description="FK to transaction_categories")
    recurrence: FisRecurrence = Field(
        default=FisRecurrence.monthly,
        description="How often money moves in this category. Drives the averaging and the "
                    "legend printed beside the line",
    )
    stated_annual_amount: Optional[Decimal] = Field(
        default=None,
        description="The attorney's own annual figure, which wins over anything derived. "
                    "Needed when the production does not reach back a full year: the "
                    "statements can show what was paid, but only a person can say what the "
                    "bill is",
    )
    note: Optional[str] = Field(
        default=None,
        description="Extra legend beside the line when the recurrence alone does not explain "
                    "it — 'escrowed with the mortgage', 'paid by employer'",
    )


class FisCategorySettingInDB(FisCategorySetting):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


# ── Filing by rule ───────────────────────────────────────────────────────────


class TransactionCategoryRule(BaseModel):
    """
    A keyword that files a transaction under a category.

    Two layers, as with tags: ``matter_id`` of None is a firm-wide rule; a value
    scopes it to one case, for the client whose EXXON lines are revenue rather
    than fuel.
    """
    matter_id: Optional[int] = Field(
        default=None,
        description="None for a firm-wide rule; a matter id scopes it to that case",
    )
    pattern: str = Field(
        ...,
        min_length=3,
        description="Matched case- and punctuation-insensitively against the description and "
                    "the counterparty, so WALMART finds 'WAL-MART #1234' and 'WALMART.COM'",
    )
    category_id: int = Field(..., description="Where a matching line is filed")
    priority: int = Field(
        default=100,
        description="Lower fires first. WALMART PHARMACY must beat WALMART, or medical "
                    "spending lands in household supplies",
    )
    applies_to: str = Field(
        default="any",
        description="any | credit | debit. PAYROLL arriving is income; PAYROLL leaving is a "
                    "business expense",
    )
    is_active: bool = Field(default=True, description="Retire a rule without losing its history")
    note: Optional[str] = Field(default=None, description="Why the rule exists, for whoever inherits it")


class TransactionCategoryRuleInDB(TransactionCategoryRule):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)


class PayeeClassification(BaseModel):
    """
    What the firm has decided about a payee: creditor, or vendor.

    The creditor-discovery scan cannot tell "Online Payment To Mr. Cooper" (a
    mortgage servicer) from "Online Payment To Frontier" (an ISP) — the two are
    the same sentence, and the difference lives outside the text. This is where
    the answer is kept once somebody gives it.

    Both verdicts are stored, because both are worth keeping. ``creditor`` puts
    a payee on the report; ``not_creditor`` takes it off permanently. Without
    the second, the same utilities are re-triaged on every matter until nobody
    reads the list at all.
    """
    matter_id: Optional[int] = Field(
        default=None,
        description="None for the firm's answer, offered on every matter; a matter id for a "
                    "judgment about one household",
    )
    pattern: str = Field(
        ...,
        min_length=3,
        description="The normalized payee, matched case- and punctuation-blind on word "
                    "boundaries — the same matching transaction_category_rules uses",
    )
    classification: str = Field(
        ...,
        description="creditor | not_creditor",
    )
    creditor_name: Optional[str] = Field(
        default=None,
        description="What to call it on a motion. The normalized payee is a scraped fragment "
                    "('CITI CARD ONLINE CITICTP'); this is what a person would write",
    )
    creditor_type: Optional[str] = Field(
        default=None,
        description="credit_card | loan | mortgage | line_of_credit | other. It changes what "
                    "you ask for: a card means statements, a mortgage means a payoff and a note",
    )
    note: Optional[str] = Field(default=None, description="Why, for whoever inherits it")
    is_active: bool = Field(default=True, description="Retire a ruling without losing its history")
    decided_by_staff_id: Optional[int] = Field(
        default=None,
        description="Who decided. A not_creditor ruling suppresses evidence from a report that "
                    "backs a motion, so it is attributable",
    )


class PayeeClassificationInDB(PayeeClassification):
    id: int = Field(..., description="Primary key, set by the database")
    created_at: datetime = Field(..., description="Set by the database")
    updated_at: Optional[datetime] = Field(default=None)
    model_config = ConfigDict(from_attributes=True)

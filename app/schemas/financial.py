"""
app/schemas/financial.py - Request and response schemas for statement ingestion.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from util import amount_filter
from db.models.financial import (
    AccountOwnership,
    AccountType,
    DateProvenance,
    ProductionResponsibility,
    InventoryExclusionReason,
    PropertyCharacter,
    StatementBoundary,
    StatementReviewStatus,
)


class StatementIngestJobResponse(BaseModel):
    """A queued statement extraction, or a document a person should split first.

    Two outcomes on one endpoint, told apart by ``status``. The second used to
    be a 413 refusal: past 50 pages or 8 statements the automatic import cannot
    work, and saying so was right about the import and offered nothing instead.
    Now the file is kept, recorded as produced, and offered to the splitting
    bench — where a person can split it, or decline and leave it unsplit.
    """
    id: Optional[str] = Field(
        default=None,
        description="Job id to poll. NULL when the document was routed to the bench instead, "
                    "because no extraction was queued")
    status: str = Field(
        ...,
        description="queued | running | succeeded | failed | routed_to_bench")
    source_document_id: Optional[int] = Field(
        default=None,
        description="The as-produced record, when routed to the bench. The file is kept "
                    "whether or not anybody splits it: it was served on us")
    route_reason: Optional[str] = Field(
        default=None,
        description="Why a person should split it, in words they can disagree with")
    warnings: list[str] = Field(
        default_factory=list,
        description="What the upload looks like before anything reads it — chiefly that a PDF "
                    "appears to hold several statements. Advisory: the ingest is already "
                    "queued, because a document holding several statements is legal input and "
                    "blocking it would trade a real workflow for a rare one",
    )


class StatementIngestOutcome(BaseModel):
    """What became of one statement found in the uploaded document."""
    status: str = Field(..., description="auto_accepted | needs_review | duplicate | error")
    statement_id: Optional[int] = None
    account_id: Optional[int] = None
    institution: Optional[str] = None
    period: Optional[list[str]] = Field(default=None, description="[start, end] as ISO dates")
    transactions: Optional[int] = None
    reconciled: Optional[bool] = None
    delta: Optional[str] = Field(default=None, description="Printed close minus computed, as a string")
    bates_first: Optional[str] = Field(default=None, description="Stamp on this statement's first page")
    bates_last: Optional[str] = Field(default=None, description="Stamp on this statement's last page")
    bates_gaps: list[str] = Field(
        default_factory=list,
        description="Numbers missing from the run inside this statement — pages absent from the production",
    )
    error: Optional[str] = None


class BatesSeriesSummary(BaseModel):
    """The Bates run found across an uploaded document."""
    prefix: str = Field(..., description="Letters before the number, e.g. 'KF'. Empty for a bare numeric stamp")
    separator: str = Field(..., description="What the document prints between prefix and digits")
    digits: int = Field(..., description="Zero-padded width of the numeric part")
    first: Optional[str] = None
    last: Optional[str] = None
    pages_stamped: int
    unstamped_pages: list[int] = Field(
        default_factory=list[int],
        description="Pages carrying no readable stamp. Their lines get no citation — a number is never "
                    "interpolated from the neighbours, because it would not appear on the document",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Numbers missing from the run that no page in hand accounts for",
    )
    confidence: str = Field(..., description="'high' when the run is unbroken and covers the document")


class StatementIngestSummary(BaseModel):
    """The result of ingesting one PDF, which may hold several statements."""
    statements_found: int
    auto_accepted: int
    needs_review: int
    results: list[StatementIngestOutcome] = Field(default_factory=list[StatementIngestOutcome])
    bates: Optional[BatesSeriesSummary] = Field(
        default=None,
        description="The Bates series detected across the document; None when it is not a production copy",
    )


class StatementJobStatusResponse(BaseModel):
    """
    A statement ingest job being polled.

    Separate from the intake job endpoint because the result shapes differ —
    polling a statement job there would try to read it as a case-style preview
    and report a spurious failure.
    """
    id: str
    status: str = Field(..., description="queued | running | succeeded | failed")
    result: Optional[StatementIngestSummary] = None
    error: Optional[str] = None


class FinancialAccountResponse(BaseModel):
    id: int
    matter_id: int
    institution: str
    account_type: AccountType
    account_number_last4: Optional[str]
    account_number_masked: Optional[str]
    name_on_account: Optional[str]
    opposing_party_id: Optional[int]
    production_responsibility: ProductionResponsibility
    ownership: AccountOwnership
    property_character: Optional[PropertyCharacter]
    purpose: Optional[str]
    notes: Optional[str]
    antecedent_account_id: Optional[int]
    is_closed: bool
    currency: str = "USD"
    excluded_from_inventory: bool = False
    inventory_exclusion_reason: Optional[InventoryExclusionReason] = None
    inventory_exclusion_note: Optional[str] = None
    inventory_excluded_by: Optional[str] = None
    inventory_excluded_at: Optional[datetime] = None


class InventoryExclusionRequest(BaseModel):
    """
    Keep an account on the inventory but out of its totals, or put it back.

    Its own request rather than a field on the account PATCH, because the
    server stamps who did it and when — a judgment that moves the net of an
    estate needs an author, and a client-supplied name is not one.
    """
    excluded: bool = Field(..., description="True to leave the account out of the totals")
    reason: Optional[InventoryExclusionReason] = Field(
        default=None, description="Why. Optional; 'other' requires a note")
    note: Optional[str] = Field(
        default=None, description="The explanation when the reason is 'other'")


class FinancialAccountUpdateRequest(BaseModel):
    """
    Attorney corrections to an account.

    Characterization and ownership are judgments, never extracted — they are
    only ever set here.
    """
    institution: Optional[str] = None
    account_type: Optional[AccountType] = None
    account_number_last4: Optional[str] = None
    account_number_masked: Optional[str] = None
    name_on_account: Optional[str] = None
    opposing_party_id: Optional[int] = None
    production_responsibility: Optional[ProductionResponsibility] = None
    ownership: Optional[AccountOwnership] = None
    property_character: Optional[PropertyCharacter] = None
    purpose: Optional[str] = None
    notes: Optional[str] = None
    antecedent_account_id: Optional[int] = Field(
        default=None,
        description="The account this one succeeds, so a reissued card reads as one history",
    )
    is_closed: Optional[bool] = None
    currency: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z]{3}$",
        description="ISO 4217 code. Changing it relabels the figures; it converts nothing")

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class FinancialAccountCreateRequest(BaseModel):
    """
    An account somebody adds by hand.

    Two reasons it exists, and the second is the useful one:

    * **An unprocessable scrap** — a receipt from a car floorboard naming an
      account nothing else mentions. There is no statement to ingest, and the
      account still belongs on the matter.
    * **Taking a row off the undisclosed list.** That report is rebuilt from the
      transactions every time, so nothing can be ticked off it. Adding the
      account is what resolves it: characterize it ``no_marital_interest`` and
      the scan stops reporting a $5 repayment to a sibling, or characterize it
      ``community`` and it joins the compliance grid with no statements against
      it — which is precisely what a motion to compel is made of.
    """
    institution: str = Field(..., min_length=1, max_length=200)
    account_type: AccountType
    account_number_last4: Optional[str] = Field(default=None, max_length=4)
    account_number_masked: Optional[str] = Field(default=None, max_length=60)
    name_on_account: Optional[str] = Field(default=None, max_length=200)
    ownership: AccountOwnership = AccountOwnership.unknown
    property_character: Optional[PropertyCharacter] = Field(
        default=None,
        description="Argued, never extracted. `no_marital_interest` takes the account off the "
                    "undisclosed list and out of the compliance grid and the inventory",
    )
    production_responsibility: ProductionResponsibility = ProductionResponsibility.unknown
    opposing_party_id: Optional[int] = None
    purpose: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)


class StatementBatesRequest(BaseModel):
    """Bates numbers typed by hand, when detection found none."""

    first: str = Field(
        ..., min_length=1, max_length=50,
        description="The stamp on the statement's first page, exactly as printed")
    last: Optional[str] = Field(
        default=None, max_length=50,
        description="The stamp on its last page. The span must match the pages in hand — the "
                    "arithmetic is the check, because being off by one mis-cites every line")


class StatementBatesResult(BaseModel):
    statement: "StatementResponse"
    pages_numbered: int = Field(..., description="Pages that received a number")


class StatementBalanceRequest(BaseModel):
    """
    An opening or closing balance read off the page, replacing a misread one.

    Either end, or both. Omitting one leaves it alone, which is why these are
    Optional rather than defaulting to the current value: a null here means
    "do not touch", and the service distinguishes that from a change.
    """

    beginning_balance: Optional[Decimal] = Field(
        default=None, description="The opening balance as printed. Omit to leave it alone")
    ending_balance: Optional[Decimal] = Field(
        default=None, description="The closing balance as printed. Omit to leave it alone")
    reason: Optional[str] = Field(
        default=None, max_length=500,
        description="Why it is being changed, kept on the flag the correction leaves")

    @model_validator(mode="after")
    def _one_end_at_least(self) -> "StatementBalanceRequest":
        if self.beginning_balance is None and self.ending_balance is None:
            raise ValueError("Give a beginning balance, an ending balance, or both")
        return self


class MotionTextResponse(BaseModel):
    """The missing statements as a paragraph for a motion to compel."""

    text: str = Field(..., description="Empty when nothing is missing, so the caller can say so")
    accounts: int = Field(..., description="Accounts with something missing")
    gaps: int = Field(..., description="Separate periods named across them")


class MergeConflict(BaseModel):
    """One reason a merge is unsafe, or worth a second look."""
    code: str = Field(..., description="SAME_ACCOUNT | DIFFERENT_MATTER | PERIOD_OVERLAP | "
                                       "BATES_OVERLAP | LAST4_MISMATCH | TYPE_MISMATCH")
    blocking: bool = Field(..., description="True when force cannot override it")
    detail: str = Field(..., description="What was found, in the words the attorney needs")


class AccountMergeRequest(BaseModel):
    """Move everything on this account onto another, then delete this one."""
    target_account_id: int = Field(..., description="The account to keep")
    force: bool = Field(
        default=False,
        description="Proceed despite non-blocking conflicts. Never overrides a blocking one",
    )


class AccountMergePreview(BaseModel):
    """What a merge would do, and what stands in its way."""
    source_account_id: int
    target_account_id: int
    source_label: str
    target_label: str
    statements_to_move: int
    transactions_to_move: int
    conflicts: list[MergeConflict] = Field(default_factory=list[MergeConflict])
    can_merge: bool = Field(..., description="True when nothing blocking was found")
    needs_force: bool = Field(..., description="True when only non-blocking conflicts remain")


class AccountMergeResult(BaseModel):
    statements_moved: int
    transactions_moved: int
    target: FinancialAccountResponse


class StatementResponse(BaseModel):
    id: int
    financial_account_id: int
    matter_id: int
    period_start: date
    period_end: date
    beginning_balance: Optional[Decimal]
    ending_balance: Optional[Decimal]
    computed_ending_balance: Optional[Decimal]
    reconciled: bool
    reconciliation_delta: Optional[Decimal]
    printed_totals: dict[str, Any]
    portfolio_summary: Optional[dict[str, Any]] = Field(
        default=None,
        description="A brokerage statement's own change-in-value block. Wanted on screen "
                    "for the same reason the filename is: it is the first thing to read "
                    "when a brokerage statement will not tie, because the extraction "
                    "deliberately omits the trades and this is what it was checked against",
    )
    flags: list[dict[str, Any]]
    review_status: StatementReviewStatus
    boundary: StatementBoundary
    storage_path: Optional[str]
    source_job_id: Optional[str]
    ingested_by_staff_id: int
    created_at: datetime
    source_filename: Optional[str] = Field(
        default=None,
        description="Name of the uploaded file. Storage renames it to the job id, so without this "
                    "there is nothing tying an exception back to the document or the import log",
    )
    bates_first: Optional[str] = Field(default=None, description="Stamp on this statement's first page")
    bates_last: Optional[str] = Field(default=None, description="Stamp on this statement's last page")


class StatementReviewRequest(BaseModel):
    """Body for PATCH /statements/{id}/review."""
    review_status: StatementReviewStatus = Field(
        ...,
        description="'accepted' keeps the statement as-is; 'rejected' deletes it, its transactions, "
                    "and — when nothing of value would go with it — the account it created",
    )


class StatementRejectResult(BaseModel):
    """What a rejection removed."""
    statement_id: int
    financial_account_id: int
    transactions_deleted: int
    account_deleted: bool
    account_kept_reason: Optional[str] = Field(
        default=None,
        description="Why the emptied account survived, when it did — other statements, a place in "
                    "an account history, or a characterization someone recorded",
    )


class StatementReviewResponse(BaseModel):
    """
    The outcome of reviewing a statement.

    Accepting returns the statement. Rejecting deletes it, so there is no
    statement left to return and ``discarded`` says what went instead.
    """
    statement: Optional[StatementResponse] = None
    discarded: Optional[StatementRejectResult] = None


class TransactionResponse(BaseModel):
    id: int
    statement_id: int
    financial_account_id: int
    line_no: int
    transaction_date: Optional[date]
    posted_date: Optional[date]
    date_provenance: DateProvenance
    description: str
    description_lines: list[str]
    counterparty: Optional[str]
    location: Optional[str]
    amount: Decimal
    running_balance: Optional[Decimal]
    category: Optional[str]
    category_id: Optional[int]
    physical_page_number: Optional[int]
    bates_number: Optional[str]
    check_number: Optional[str]
    flags: list[dict[str, Any]]
    deleted_at: Optional[datetime] = None
    deleted_by_staff_id: Optional[int] = None
    deletion_reason: Optional[str] = None


# ── Classification: categories and tags ──────────────────────────────────────


class TransactionCategoryResponse(BaseModel):
    """One node of the firm-wide category tree."""
    id: int
    description: str
    parent_id: Optional[int]
    display_order: int
    include_in_fis: bool
    is_active: bool
    depth: int = Field(
        default=0,
        description="Levels below the root, computed on read so the picker can indent without walking",
    )
    path: str = Field(
        default="",
        description="Ancestors joined with ' > ', e.g. 'Housing > Utilities > Gas'. "
                    "A leaf name alone is ambiguous — 'Gas' is both a utility and a car expense",
    )


class TransactionCategoryWriteRequest(BaseModel):
    """Create or amend a category. Firm-wide, so admin only."""
    description: Optional[str] = None
    parent_id: Optional[int] = None
    display_order: Optional[int] = None
    include_in_fis: Optional[bool] = None
    is_active: Optional[bool] = None


class TransactionTagResponse(BaseModel):
    id: int
    matter_id: Optional[int] = Field(
        default=None,
        description="None for a firm-wide tag; a matter id scopes it to that case",
    )
    label: str
    description: Optional[str]
    color: Optional[str]
    display_order: int
    is_active: bool
    client_visible: bool = Field(
        default=False, description="Whether clients are offered this tag in the portal")
    guest_id: Optional[int] = Field(
        default=None, description="Set on a financial professional's own tag. Read-only to staff")
    guest_name: Optional[str] = Field(
        default=None, description="Whose tag it is, for labelling it on staff screens")
    usage_count: Optional[int] = Field(
        default=None,
        description="Transactions carrying this tag; populated on the tag list, not on writes",
    )


class TransactionTagWriteRequest(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None
    client_visible: Optional[bool] = Field(
        default=None,
        description="Offer this tag to clients in the portal. Opt-IN: the firm's list carries "
                    "litigation strategy, so a tag reaches a client only when somebody says so",
    )


# ── Search ───────────────────────────────────────────────────────────────────


class TransactionSearchRequest(BaseModel):
    """
    A filter over one matter's transactions.

    POST rather than GET: the filter carries three id arrays and a free-text
    term, which as a query string is both unreadable and liable to run past a
    URL length limit on a large tag selection.
    """
    account_ids: Optional[list[int]] = Field(
        default=None,
        description="Narrow to these accounts; omit for every account on the matter",
    )
    date_from: Optional[date] = Field(default=None, description="Earliest transaction date, inclusive")
    date_to: Optional[date] = Field(default=None, description="Latest transaction date, inclusive")
    category_ids: Optional[list[int]] = Field(default=None, description="Categories to include")
    include_subcategories: bool = Field(
        default=True,
        description="Expand each category to its descendants. On by default — picking 'Housing' and "
                    "getting nothing because every line is filed under 'Rent' is not a useful filter",
    )
    uncategorized: bool = Field(
        default=False,
        description="Only lines with no category — the work queue for preparing an FIS",
    )
    tag_ids: Optional[list[int]] = Field(default=None, description="Tags to filter on")
    tag_match_all: bool = Field(
        default=False,
        description="Require every listed tag rather than any of them",
    )
    untagged: bool = Field(default=False, description="Only lines carrying no tag at all")
    include_deleted: bool = Field(
        default=False,
        description="Show lines somebody dropped from their statement. Off by default — a dropped "
                    "line must never reach a total or an exhibit through an oversight",
    )
    text: Optional[str] = Field(default=None, description="Case-insensitive substring of the description")
    check_number: Optional[str] = Field(
        default=None,
        description="One check, by number. A check is the only debit that does not say where the "
                    "money went, so this traces a payment back to the instrument",
    )
    checks_only: bool = Field(
        default=False,
        description="Every check on the account and nothing else",
    )
    # STRINGS, because the sign the user types is an instruction and not part of
    # the number. A plain figure searches by magnitude and matches both
    # directions -- which is what finds an offsetting pair, a transfer out of
    # one produced account and the same figure arriving in another. A leading
    # "-" restricts to money out as the statement printed it, "+" to money in.
    # Both boxes are magnitudes either way, so the smaller figure goes first
    # whichever direction is being searched. See util/amount_filter.py.
    amount_min: Optional[str] = Field(
        default=None, max_length=24,
        description="Smallest amount, as typed: 5000 matches either direction, -5000 is money "
                    "out, +5000 is money in",
    )
    amount_max: Optional[str] = Field(
        default=None, max_length=24,
        description="Largest amount, as typed. Same rules as amount_min, and the two must not "
                    "ask for different directions",
    )
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _amounts_are_readable(self) -> "TransactionSearchRequest":
        """
        Refuse an unreadable amount range here, at the boundary.

        The service resolves it again and is the authority; this exists so a
        typo answers 422 with the sentence that says what to type, rather than
        reaching a query that would quietly match something else.
        """
        amount_filter.resolve(self.amount_min, self.amount_max)
        return self


class ExhibitExportRequest(BaseModel):
    """
    How to render a report that takes no filter.

    Separate from TransactionExportRequest, which inherits the whole search so
    the export is provably the same set the screen showed. A report with no
    criteria — the undisclosed-account list is the whole of what was found —
    needs only the two fields.
    """
    format: str = Field(
        default="csv",
        pattern="^(csv|md|docx|pdf)$",
        description="csv is the clean extraction; md, docx, and pdf are full exhibits "
                    "carrying the caption and the verification notice",
    )
    exhibit_name: str = Field(
        default="Accounts Referenced But Not Produced",
        max_length=120,
        description="Titles the exhibit and names the file",
    )


class TransactionExportRequest(TransactionSearchRequest):
    """
    A filter plus how to render it.

    Inherits every field of the search so the export is provably the same set
    the screen showed — a divergence between the two would be invisible and
    would surface as an exhibit nobody can reproduce. ``limit`` and ``offset``
    are inherited but ignored: an export covers every matching line.
    """
    format: str = Field(
        default="csv",
        pattern="^(csv|md|docx|pdf)$",
        description="csv is the clean extraction; md, docx, and pdf are full exhibits "
                    "carrying the caption and the verification notice",
    )
    exhibit_name: str = Field(
        default="Financial Summary",
        max_length=120,
        description="Titles the exhibit — \"Petitioner's Financial Summary\". Also the filename",
    )


class TransactionSearchRow(TransactionResponse):
    """A transaction plus the context a result row displays."""
    tag_ids: list[int] = Field(default_factory=list[int], description="All tags on this line, for the tag cloud and bulk tagging")
    client_tag_ids: list[int] = Field(
        default_factory=list[int],
        description="Of those, the ones the CLIENT applied (044). Staff see which assertions are "
                    "the client's, because an exhibit should never quietly rest on a guess")
    institution: Optional[str] = None
    account_last4: Optional[str] = None


class TransactionSearchResponse(BaseModel):
    total: int = Field(..., description="Every matching line, not just this page — how big the exhibit is")
    items: list[TransactionSearchRow]
    sum_amount: Decimal = Field(
        ...,
        description="Signed total of the rows on this page. The page's own total: summing every match "
                    "would mean a second full query, and this is what is on screen to read against",
    )


# ── Bulk classification ──────────────────────────────────────────────────────


class BulkCategorizeRequest(BaseModel):
    """File a set of transactions under a category, or clear it with null."""
    transaction_ids: list[int] = Field(..., min_length=1)
    category_id: Optional[int] = Field(
        default=None,
        description="The category to file under; null clears it",
    )


class BulkTagRequest(BaseModel):
    """
    Apply or remove one tag across a set of transactions.

    Bulk because the workflow is "filter down to the exhibit, then tag the
    result" — a line at a time over a year of statements is unusable.
    """
    transaction_ids: list[int] = Field(..., min_length=1)
    tag_id: int
    remove: bool = Field(default=False, description="Remove the tag instead of applying it")


class ReviewRequest(BaseModel):
    """Confirm that automatic assignments were checked and left standing."""
    transaction_ids: list[int] = Field(..., min_length=1)


class BulkResultResponse(BaseModel):
    changed: int = Field(..., description="Rows actually altered; re-applying an existing tag is a no-op")


class TransactionUpdateRequest(BaseModel):
    """
    Correct a value on an ingested line.

    Extraction misreads things — a smudged digit, a description running off the
    page — so a line has to be correctable. Nothing is quietly overwritten: each
    change appends a MANUAL_CORRECTION flag naming the field, both values, and
    the person, so the original stays recoverable from the record itself.
    """
    description: Optional[str] = None
    transaction_date: Optional[date] = None
    posted_date: Optional[date] = None
    counterparty: Optional[str] = None
    location: Optional[str] = None
    amount: Optional[Decimal] = None
    running_balance: Optional[Decimal] = None
    bates_number: Optional[str] = None
    check_number: Optional[str] = None
    physical_page_number: Optional[int] = None
    reason: Optional[str] = Field(
        default=None,
        description="Why the change was made, e.g. 'corrected against page 3'. Kept on the flag",
    )


class TransactionCorrectionResponse(BaseModel):
    """The corrected line, plus the statement when the change moved the balance."""
    transaction: TransactionResponse
    statement: Optional[StatementResponse] = Field(
        default=None,
        description="Re-reconciled when an amount changed — that is the point of allowing the edit. "
                    "None when the correction did not touch the arithmetic",
    )


class TransactionAddRequest(BaseModel):
    """
    A line to add to a statement that the import did not produce.

    The alternative was an INSERT by hand: a row typed into the database at zero
    and then corrected through the app until the statement balanced. That works
    and leaves no trace anybody reading the exhibit can see, which is the whole
    reason this exists — every line added here carries a ``MANUAL_ENTRY`` flag
    naming the person and their reason.

    Fields match what a correction may change, so one line can be added and then
    corrected by the same rules.
    """
    description: str = Field(
        ..., min_length=1, max_length=500,
        description="What the line is, as printed on the statement",
    )
    amount: Decimal = Field(
        ...,
        description="Signed by its effect on the printed balance: money out negative, money in "
                    "positive — the same convention as every extracted line",
    )
    transaction_date: Optional[date] = Field(default=None, description="Date printed on the line")
    posted_date: Optional[date] = Field(default=None, description="Posting date, when printed separately")
    counterparty: Optional[str] = Field(default=None, max_length=200, description="Payee, when known")
    location: Optional[str] = Field(default=None, max_length=200, description="City/state on the line")
    running_balance: Optional[Decimal] = Field(
        default=None, description="Balance printed after this line, when the statement prints one")
    check_number: Optional[str] = Field(default=None, max_length=20, description="Check number as printed")
    physical_page_number: Optional[int] = Field(
        default=None, ge=1, description="Page of the source PDF this line is printed on")
    bates_number: Optional[str] = Field(
        default=None, max_length=50,
        description="Stamp on that page, copied from it — never constructed from a neighbouring page",
    )
    reason: Optional[str] = Field(
        default=None, max_length=500,
        description="Why the line is being added, e.g. 'printed on page 7, missed by the import'. "
                    "Recorded on the line with the person's name",
    )


class TransactionDeleteRequest(BaseModel):
    """Body for dropping a line from its statement."""
    reason: Optional[str] = Field(
        default=None,
        description="Why the line is not on the statement, e.g. 'duplicate of line 4'. "
                    "Recorded on the line with the person's name",
    )


class AccountDeletePreview(BaseModel):
    """What deleting an account would take with it."""
    account_id: int
    account_label: str
    statements: int
    transactions: int
    periods: list[str] = Field(default_factory=list, description="Each statement's period, oldest first")
    warnings: list[str] = Field(
        default_factory=list,
        description="Reasons to stop and look: a characterization, a recorded owner, notes "
                    "somebody wrote, or a place in an account history. Warnings, not blocks — "
                    "this is a deliberate act, not an automatic cleanup",
    )


class ReferencedInstitutionResponse(BaseModel):
    """
    A bank the wires name that this matter has no account at.

    Reported separately from the accounts because a wire prints the sending
    INSTITUTION and never the sending account — there is no number to key on,
    which is exactly why an account-shaped report missed $198,101.18 of incoming
    wires while catching a $500 transfer between two accounts we already had.
    """
    institution: str = Field(..., description="The bank as the wire printed it")
    aba: Optional[str] = Field(
        default=None,
        description="Its routing number. Checksummed, so it is a reliable identity where a "
                    "name spelled two ways is not",
    )
    wires: int = Field(..., description="Wire lines naming it")
    same_party_wires: int = Field(
        ...,
        description="Wires where the sender and the receiver are the same person. This is the "
                    "finding: money moving out of an account they control and into one we hold",
    )
    money_in: Decimal
    money_out: Decimal
    net: Decimal
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    seen_on: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)

    possible_match: Optional[str] = Field(
        default=None,
        description="An account on this matter whose institution is close enough that this may "
                    "already be produced. NEVER suppressed on a near match -- hiding a "
                    "genuinely undisclosed account is the worse failure -- so the row stays, "
                    "says which account it probably is, and is left out of the exhibit",
    )
class StatementRetryResult(BaseModel):
    """
    What a re-import discarded, and the job now reading the document again.

    The counts are the point of the response: "Retry" on one statement can
    legitimately discard five, because a combined statement holds five accounts
    and the file is re-read as a whole. A caller that reported only the one the
    user clicked would understate what just happened.
    """
    job_id: str = Field(..., description="Poll this the same way an upload is polled")
    statements_discarded: int = Field(
        ...,
        description="Every statement the original upload produced — not just the one clicked. "
                    "Re-reading the PDF recreates all of them, so leaving the siblings would "
                    "duplicate each of them",
    )
    transactions_discarded: int
    accounts_deleted: int = Field(
        ...,
        description="Accounts removed because nothing of value was left on them. An account "
                    "carrying a characterization is kept",
    )
    source_filename: Optional[str] = Field(default=None, description="The document being re-read")


class StatementBoundaryRequest(BaseModel):
    """Mark a statement as an account's first, its last, or neither."""
    boundary: StatementBoundary = Field(
        ...,
        description="opening = nothing is missing before this one; closing = nothing after; "
                    "intermediate = expect statements on both sides",
    )


class ComplianceExportRequest(BaseModel):
    """How to render the compliance matrix, and which of the two reports."""
    format: str = Field(default="csv", pattern="^(csv|md|docx|pdf)$")
    exhibit_name: str = Field(default="Statements Produced and Not Produced", max_length=200)
    side: Optional[str] = Field(
        default=None,
        pattern="^(client|opposing)$",
        description="Which obligation the exhibit measures. Omit for every account over the "
                    "range the data covers — useful internally, but a document filed with a "
                    "court almost always wants one side named",
    )


class ComplianceCell(BaseModel):
    """One statement, in the month it closes in."""
    statement_id: int
    bates: Optional[str] = Field(default=None, description="Stamp on the statement's first page")
    bates_last: Optional[str] = None
    source_filename: Optional[str] = None
    boundary: StatementBoundary
    review_status: StatementReviewStatus
    reconciled: bool
    period_start: date
    period_end: date
    has_pdf: bool = Field(..., description="False once the source has been purged from storage")


class ComplianceGap(BaseModel):
    """Days no produced statement accounts for."""
    start: date
    end: date
    days: int


class ComplianceAccount(BaseModel):
    """One row of the matrix, and the coverage summary printed beneath it."""
    account_id: int
    production_responsibility: ProductionResponsibility
    institution: str
    last4: Optional[str]
    account_type: AccountType
    type_label: str
    label: str
    is_closed: bool
    cells: dict[str, list[ComplianceCell]] = Field(
        default_factory=dict,
        description="Keyed \"YYYY-MM\" on the month each statement CLOSES in — a period running "
                    "4 March to 5 April is the April statement, and keying on the start would "
                    "make consecutive statements look like they skip months",
    )
    opening_month: Optional[str] = Field(
        default=None, description="Month of the statement marked opening, if any",
    )
    closing_month: Optional[str] = None
    statements: int
    gaps: list[ComplianceGap] = Field(
        default_factory=list,
        description="Days between two produced statements that nothing covers. A month with a "
                    "statement in it is not a month that is covered: periods do not line up "
                    "with calendar months",
    )
    nothing_before: Optional[date] = Field(
        default=None,
        description="Earliest date produced, when the first statement is NOT marked opening — "
                    "so anything before it may exist and was not produced. Null when it is "
                    "marked, because then there is nothing before it to want",
    )
    nothing_after: Optional[date] = Field(
        default=None,
        description="Latest date produced, when the last statement is NOT marked closing",
    )


class ComplianceTotals(BaseModel):
    accounts: int
    statements: int
    gaps: int
    unassigned: int = Field(
        default=0,
        description="Accounts nobody has said who must produce. Counted rather than shown, "
                    "because an unmarked account would otherwise appear on neither report and "
                    "never be chased",
    )


class ComplianceScope(BaseModel):
    """Which obligation this report is measured against."""
    side: Optional[str] = Field(
        default=None,
        description="client = what our side must produce; opposing = what theirs must; null = "
                    "every account, over the range the data itself covers",
    )
    since: Optional[date] = Field(
        default=None,
        description="Earliest date the governing request reaches back to. Null when the matter "
                    "has no look-back recorded, and then the outer gaps can only be stated as "
                    "open-ended dates",
    )
    through: Optional[date] = Field(default=None, description="Today — an obligation runs to the present")
    bounded: bool = Field(..., description="Whether a look-back date was found for this side")


class InventoryRow(BaseModel):
    """One account on the inventory, with the balance its last statement printed."""

    account_id: int
    institution: str
    account_number_last4: Optional[str] = None
    account_type: str
    held_by: str = Field(
        ..., description="P, R, J, 3P or ? — from the account's ownership and the matter's "
                         "client alignment. C/O when no alignment is recorded")
    as_of: Optional[date] = Field(
        default=None, description="Period end of the statement the balance came from")
    balance: Optional[str] = Field(
        default=None,
        description="Exact decimal string. **A liability is negative here** — it reduces the "
                    "estate — while the statement itself prints a positive amount owed. None "
                    "when no statement is on file")
    bates: Optional[str] = Field(
        default=None, description="Stamp on that statement's first page, where one was detected")
    statement_id: Optional[int] = None
    is_closed: bool = False
    currency: str = Field(default="USD", description="The account's currency")
    native_balance: Optional[str] = Field(
        default=None,
        description="The balance as printed, in the account's currency, when that is not USD. "
                    "``balance`` is then the dollar conversion, or None with no rate")
    usd_per_unit: Optional[str] = Field(default=None, description="The rate used, when converted")
    rate_as_of: Optional[date] = None
    conversion_note: Optional[str] = Field(
        default=None, description="'Balance of PKR 10,000,000.00 converted to USD at 0.0036 "
                                  "USD/PKR' — or why it could not be")
    excluded: bool = Field(
        default=False, description="Listed, but left out of the totals by a person's decision")
    exclusion_label: Optional[str] = Field(
        default=None, description="Why, in words — the canned reason or the 'other' note")
    excluded_by: Optional[str] = None
    excluded_at: Optional[datetime] = None


class InventoryTotals(BaseModel):
    assets: str
    liabilities: str
    net: str
    accounts: int
    without_balance: int = Field(
        ..., description="Accounts with no statement on file. Listed, never totalled — an "
                         "account nobody has produced a statement for is what the reader needs "
                         "to see")
    excluded: int = Field(
        default=0, description="Accounts listed but left out of the totals by a person's decision")
    unconverted: int = Field(
        default=0, description="Foreign-currency accounts with no rate: listed, not totalled")


class CurrencyRateResponse(BaseModel):
    currency: str
    usd_per_unit: str = Field(..., description="Exact decimal string")
    as_of: date
    source: Optional[str] = None
    entered_by: Optional[str] = None


class CurrencyRateRequest(BaseModel):
    """A rate for the inventory. Dollars per ONE unit: PKR at 0.0036."""

    usd_per_unit: Decimal = Field(..., gt=0)
    as_of: date
    source: Optional[str] = Field(default=None, max_length=300)


class InventoryReport(BaseModel):
    """Every account on the matter, as an inventory of what it is worth."""

    assets: list[InventoryRow] = Field(default_factory=list)
    liabilities: list[InventoryRow] = Field(default_factory=list)
    totals: InventoryTotals
    legend: list[tuple[str, str]] = Field(
        default_factory=list, description="Letter and what it means, for the Held by column")
    warnings: list[str] = Field(
        default_factory=list,
        description="What the report could not establish — chiefly a missing client alignment")
    currencies: list[str] = Field(
        default_factory=list, description="Foreign currencies held on the matter, needing a rate")
    rates: list[CurrencyRateResponse] = Field(
        default_factory=list, description="The rates recorded for them")


class ComplianceMatrix(BaseModel):
    """
    What the production holds, by account and month — and what it does not.

    The years span the whole matter rather than each account, so an account with
    a shorter history shows its blank years instead of ending early. The blanks
    are the report.
    """
    years: list[int] = Field(default_factory=list)
    accounts: list[ComplianceAccount] = Field(default_factory=list)
    totals: ComplianceTotals
    scope: ComplianceScope


class StatementPdfUrlResponse(BaseModel):
    """
    Where to find the document a statement was read from.

    Carries a page hint because one upload routinely holds a whole production —
    twelve statements, sixty pages — and opening at page 1 leaves the reader
    hunting for the month they clicked on.
    """
    url: str = Field(..., description="Signed URL; the signature is the authorization")
    expires_in: int = Field(..., description="Seconds the URL remains valid")
    page: Optional[int] = Field(
        default=None,
        description="The first page carrying one of this statement's transactions — an "
                    "approximation of where it begins, not its first page. A statement "
                    "usually opens with a summary page or two before any line appears, so "
                    "the true start is at or just before this",
    )
    source_filename: Optional[str] = Field(
        default=None,
        description="The uploaded file's own name. Storage renames every upload to a job id, "
                    "so this is the only thing tying the tab that opens back to a real file",
    )


class CreditorResponse(BaseModel):
    """
    A payee the matter pays that no produced statement accounts for.

    A payment names a payee and, almost never, a number — which is why this is
    keyed on the payee rather than on a last four. Whether that payee is a
    creditor comes from outside the description: the category a person filed the
    payments under, or a standing ruling about the payee. ``reason`` says which,
    on every row, because "American Express" and "the City of Lewisville" arrive
    here looking identical and only one of them is a finding.
    """
    payee: str = Field(..., description="The payee as the scan reads it across every payment, "
                                        "or the ruling's pattern once one exists")
    creditor_name: Optional[str] = Field(
        default=None,
        description="What to call it on a motion. The scraped payee is a fragment; this is the "
                    "name a person wrote against it",
    )
    creditor_type: Optional[str] = Field(
        default=None,
        description="credit_card | loan | mortgage | line_of_credit | other. It changes what you "
                    "request: a card means statements, a mortgage means a payoff and a note",
    )
    reason: str = Field(
        ...,
        description="liability_category — payments are filed under a category naming a debt; "
                    "classified — a standing ruling says this payee is a creditor; "
                    "unreviewed — nobody has said either way, so this is a question not a finding",
    )
    classification_id: Optional[int] = Field(
        default=None,
        description="The ruling that put it here, so the UI can offer to change it",
    )
    payments: int = Field(..., description="Payment lines to this payee")
    money_out: Decimal = Field(..., description="Total paid. The ranking key: an unproduced card "
                                                "serviced at $4,000 a month is not the same "
                                                "finding as one paid twice")
    last4: list[str] = Field(
        default_factory=list,
        description="Account digits any payment happened to print. Usually empty — that is the "
                    "whole reason payees need their own report",
    )
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    seen_on: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    possible_match: Optional[str] = Field(
        default=None,
        description="An account on this matter whose institution is close enough that this may "
                    "already be produced. NEVER suppressed on a near match -- hiding a "
                    "genuinely undisclosed account is the worse failure -- so the row stays, "
                    "says which account it probably is, and is left out of the exhibit",
    )


# What a custodian ruling may say it holds. Mirrors the CHECK in 036 -- the
# database is the authority, and this exists so a typo comes back as a sentence
# naming the bad value instead of a 500 from PostgREST.
_HOLDS = frozenset({
    "deposit", "brokerage", "crypto", "retirement",
    "credit_card", "line_of_credit", "loan", "other",
})

class UndisclosedReport(BaseModel):
    """
    Everything the production names but does not contain, by how it was found.

    Four lists because there are four shapes of evidence, and collapsing them
    would misrepresent three of them. ``candidates`` in particular is a work
    queue, not a finding, and is the one list that never reaches an exhibit.
    """
    accounts: list["UndisclosedAccountResponse"] = Field(default_factory=list)
    institutions: list[ReferencedInstitutionResponse] = Field(default_factory=list)
    creditors: list[CreditorResponse] = Field(default_factory=list)
    candidates: list[CreditorResponse] = Field(
        default_factory=list,
        description="Payees nobody has ruled on, largest first. A question for a person, never "
                    "an assertion — a utility and a card issuer are indistinguishable here",
    )
    platforms: list["ValuePlatformResponse"] = Field(
        default_factory=list,
        description="Wallets, brokerages and exchanges holding value that no produced statement "
                    "accounts for. A finding, not a queue: unlike a creditor, nobody has to rule "
                    "on whether Venmo holds money",
    )


class ValuePlatformResponse(BaseModel):
    """
    A counterparty that holds value for our party and produced no statement.

    Venmo, PayPal, Cash App, Coinbase, Robinhood, a brokerage. Not the creditor
    finding wearing different words: a creditor is only ever paid, so only money
    leaving means anything, while a custodian is a container and traffic in
    either direction proves the container exists.

    Unlike a creditor this needs no ruling from anybody. Whether Mr. Cooper is a
    lender cannot be read off a description; whether Venmo holds a balance is
    the same answer on every matter that will ever be opened, so these are
    seeded firm-wide and only the long tail is ever triaged.
    """
    platform: str = Field(..., description="What to call it on a motion")
    pattern: str = Field(..., description="The ruling pattern that matched the descriptions")
    holds: list[str] = Field(
        ...,
        description="Every kind of account this platform can hold. One platform commonly "
                    "carries several and which of them exist cannot be read off a description, "
                    "so the request asks for all of them",
    )
    missing: list[str] = Field(
        ...,
        description="The kinds still unaccounted for. Computed per KIND, not per institution: "
                    "a produced PayPal balance answers the deposit half and says nothing "
                    "whatever about PayPal Credit",
    )
    produced_types: list[str] = Field(
        ...,
        description="Account types already on the matter at this platform. A row disappears "
                    "only when every kind it can hold has one",
    )
    transactions: int = Field(..., description="Lines naming the platform")
    money_out: Decimal = Field(..., description="Money that left the produced accounts for it")
    money_in: Decimal = Field(..., description="Money that came back from it")
    unreturned: Decimal = Field(
        ...,
        description="money_out less money_in. NOT A BALANCE, and it must never be labelled one: "
                    "money that did not return may be sitting on the platform, may have been "
                    "spent from it, or may have moved to another unproduced account. A negative "
                    "figure is the opposite finding — the platform was funded from somewhere "
                    "these records do not contain",
    )
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    seen_on: list[str] = Field(..., description="Produced accounts the traffic appears on")
    examples: list[str] = Field(..., description="Descriptions, verbatim, up to three")
    is_firm_wide: bool = Field(
        ...,
        description="Whether the ruling behind this row governs every case or only this one",
    )

    possible_match: Optional[str] = Field(
        default=None,
        description="An account on this matter whose institution is close enough that this may "
                    "already be produced. NEVER suppressed on a near match -- hiding a "
                    "genuinely undisclosed account is the worse failure -- so the row stays, "
                    "says which account it probably is, and is left out of the exhibit",
    )
class PayeeClassificationWriteRequest(BaseModel):
    """Record what a payee is, or stop being asked about it."""
    pattern: str = Field(
        ..., min_length=3, max_length=120,
        description="Matched case- and punctuation-blind on word boundaries, so ATT matches both "
                    "'AT&T BILL PAYMENT' and 'ATT* BILL'. Three characters minimum",
    )
    classification: str = Field(
        ..., pattern="^(creditor|custodian|not_creditor)$",
        description="creditor puts the payee on the report as a debt; custodian puts it there as "
                    "a container holding value; not_creditor removes it for good. The first two "
                    "are independent — Venmo is not a creditor AND holds a balance",
    )
    matter_id: Optional[int] = Field(
        default=None,
        description="Omit for the firm's answer, applied to every matter. A matter id for a "
                    "judgment about one household — a Zelle payee who might be a private lender",
    )
    creditor_name: Optional[str] = Field(default=None, max_length=200)
    creditor_type: Optional[str] = Field(
        default=None,
        pattern="^(credit_card|loan|mortgage|line_of_credit|other)$",
    )
    holds: list[str] = Field(
        default_factory=list,
        description="Required on a custodian and forbidden elsewhere: every kind of account the "
                    "platform can hold. deposit | brokerage | crypto | retirement | credit_card "
                    "| line_of_credit | loan | other",
    )
    note: Optional[str] = Field(default=None, max_length=300)
    is_active: bool = Field(default=True)

    @field_validator("holds")
    @classmethod
    def _known_kinds(cls, value: list[str]) -> list[str]:
        """Reject a kind the database CHECK would reject, with a sentence saying which."""
        unknown = [kind for kind in value if kind not in _HOLDS]
        if unknown:
            raise ValueError("Unknown kind of account: %s" % ", ".join(sorted(unknown)))
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _shape_matches_classification(self) -> "PayeeClassificationWriteRequest":
        """
        A custodian says what it holds; nothing else may.

        That set IS the request for production, so a custodian without one is a
        row that can never produce a usable finding. And letting a creditor
        carry one would put the same answer in two columns, which is how they
        start disagreeing.
        """
        if self.classification == "custodian" and not self.holds:
            raise ValueError(
                "A custodian must say what it holds — that list is what the request for "
                "production asks for"
            )
        if self.classification != "custodian" and self.holds:
            raise ValueError("Only a custodian carries 'holds'; a creditor uses creditor_type")
        return self


class PayeeClassificationResponse(BaseModel):
    id: int
    matter_id: Optional[int]
    pattern: str
    classification: str
    creditor_name: Optional[str]
    creditor_type: Optional[str]
    holds: list[str] = Field(default_factory=list)
    note: Optional[str]
    is_active: bool
    decided_by_staff_id: Optional[int]
    is_firm_wide: bool = Field(
        ...,
        description="True when the ruling governs every case. An editor that could not tell would "
                    "offer to change it from inside one matter and silently alter every other",
    )


class UndisclosedAccountResponse(BaseModel):
    """
    An account the produced transactions name but no produced statement covers.

    Every figure here is derived from lines that *are* in the production. The
    account itself is not: it is an inference from a printed reference, which
    is why the institution carries its own confidence flag rather than being
    presented as fact.
    """
    last4: str = Field(..., description="Last four digits — the identity, and how two spellings of "
                                        "the same account are merged")
    reference: str = Field(..., description="The longest form of the number seen in a description, "
                                            "for quoting back to the line it came from")
    institution: Optional[str] = Field(
        default=None,
        description="Named in the description when one appears there; otherwise the institution of "
                    "the account the transfer was printed on",
    )
    institution_inferred: bool = Field(
        ...,
        description="True when the institution was assumed from the statement rather than read off "
                    "it. Carries the dagger in the UI — this is the part that could be wrong",
    )
    mentions: int = Field(..., description="Transfer lines referencing this account")
    money_in: Decimal = Field(..., description="Total that arrived from this account, from the sign "
                                               "of the amounts, not the words to/from")
    money_out: Decimal = Field(..., description="Total that went to this account")
    net: Decimal = Field(..., description="money_in − money_out. Negative means the matter's "
                                          "accounts are a net source of funds to this one")
    first_seen: Optional[date] = Field(default=None, description="Earliest dated reference")
    last_seen: Optional[date] = Field(default=None, description="Latest dated reference")
    seen_on: list[str] = Field(default_factory=list,
                               description="Produced accounts whose statements name this one")
    examples: list[str] = Field(default_factory=list,
                                description="Up to three descriptions, verbatim, so the finding can "
                                            "be traced to the page it came from")


    possible_match: Optional[str] = Field(
        default=None,
        description="An account on this matter whose institution is close enough that this may "
                    "already be produced. NEVER suppressed on a near match -- hiding a "
                    "genuinely undisclosed account is the worse failure -- so the row stays, "
                    "says which account it probably is, and is left out of the exhibit",
    )
UndisclosedReport.model_rebuild()

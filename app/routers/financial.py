"""
app/routers/financial.py - Accounts, statements, and transactions on a matter.

Ingestion is queued (§11a): a statement PDF is one LLM call per statement over
a document that may hold several months, which is far too long to hold a
request open. The upload returns a job id; the worker does the work.
"""
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from db.models.financial import StatementReviewStatus, TransactionCategory, TransactionTag
from db.models.job import JobStatus
from db.repositories.financial import (
    FinancialAccountRepository,
    FinancialAccountStatementRepository,
    FinancialAccountTransactionRepository,
    TransactionCategoryRepository,
    TransactionTagRepository,
)
from db.repositories.matter import MatterRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.financial import (
    AccountDeletePreview,
    AccountMergePreview,
    AccountMergeRequest,
    AccountMergeResult,
    BulkCategorizeRequest,
    BulkResultResponse,
    BulkTagRequest,
    ExhibitExportRequest,
    FinancialAccountResponse,
    FinancialAccountUpdateRequest,
    StatementIngestJobResponse,
    StatementIngestSummary,
    StatementJobStatusResponse,
    StatementRejectResult,
    StatementResponse,
    StatementReviewRequest,
    StatementReviewResponse,
    TransactionCategoryResponse,
    TransactionCategoryWriteRequest,
    TransactionCorrectionResponse,
    TransactionDeleteRequest,
    ReviewRequest,
    TransactionResponse,
    TransactionExportRequest,
    TransactionSearchRequest,
    TransactionSearchResponse,
    TransactionTagResponse,
    TransactionTagWriteRequest,
    TransactionUpdateRequest,
    UndisclosedAccountResponse,
)
from services.account_discovery_service import account_discovery_service
from services.audit_logger import AuditLogger
from services import exhibit_service
from services.job_service import job_service
from services.statement_service import statement_service
from services.transaction_search_service import transaction_search_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["financial"])

_STAFF_ROLES = ["attorney", "admin", "paralegal"]


def _staff_id(request: Request, manager: Any) -> int:
    staff = StaffRepository(manager).get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")
    return staff.id



def _staff_and_name(request: Request, manager: Any):
    """
    The acting staff member and how their name should read in a record.

    The name is stored on the line, not just the id: these entries are read back
    years later, when a staff id means nothing to anybody.
    """
    staff = StaffRepository(manager).get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")
    name = " ".join(p for p in (staff.name.first_name, staff.name.last_name) if p).strip()
    return staff, name or "A staff member"

def _statement_response(record: Any) -> StatementResponse:
    """
    Build a statement response, lifting provenance out of the extraction blob.

    Filename and Bates range live in ``extraction`` rather than in columns of
    their own — they are provenance, never queried on — but the exceptions queue
    needs them front and centre, because "which file was this?" is the first
    question asked about a statement that will not reconcile.
    """
    extraction = record.extraction or {}
    return StatementResponse(
        **record.model_dump(),
        source_filename=extraction.get("source_filename"),
        bates_first=extraction.get("bates_first"),
        bates_last=extraction.get("bates_last"),
    )


# ── Ingestion ────────────────────────────────────────────────────────────────

@router.post("/matters/{matter_id}/statements/upload",
             response_model=StatementIngestJobResponse, status_code=202)
def upload_statement(
    matter_id: int,
    request: Request,
    file: UploadFile = File(...),
    bates_prefix: str = Form(default=""),
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> StatementIngestJobResponse:
    """
    Accept a statement PDF and return a job to poll.

    One PDF may hold several statements — a combined bank package, or months
    scanned together. Each is filed against its own account.

    ``bates_prefix`` is optional and rarely needed: the stamp is normally found
    by pattern, and the prefix only helps when a document carries two competing
    series or an unusual one.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    pdf_bytes = file.file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    try:
        job = job_service.enqueue_statement_ingest(
            manager, _staff_id(request, manager), matter_id, pdf_bytes,
            bates_prefix=bates_prefix.strip() or None,
            source_filename=(file.filename or "").strip() or None,
        )
    except Exception as e:  # noqa: BLE001 — storage failure is the only path here
        LOGGER.error("financial.upload_statement: could not queue: %s", str(e))
        raise HTTPException(status_code=502, detail="Could not store the upload for processing") from e

    return StatementIngestJobResponse(id=job.id, status=job.status.value)


@router.get("/statements/jobs/{job_id}", response_model=StatementJobStatusResponse)
def get_statement_job(
    job_id: str,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> StatementJobStatusResponse:
    """
    Poll a statement ingest. Returns the summary once the status is 'succeeded'.

    Scoped to the staff member who uploaded, like every other job poll.
    """
    job = job_service.get_for_staff(manager, job_id, _staff_id(request, manager))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    summary = None
    if job.status == JobStatus.succeeded and job.result:
        try:
            summary = StatementIngestSummary.model_validate(job.result)
        except Exception as e:  # noqa: BLE001 — a result from an older shape
            LOGGER.error("financial.get_statement_job: job=%s result no longer parses: %s", job_id, str(e))
            return StatementJobStatusResponse(
                id=job.id, status=JobStatus.failed.value,
                error="This ingest was produced by an older version — please upload again.",
            )

    return StatementJobStatusResponse(
        id=job.id, status=job.status.value, result=summary, error=job.error,
    )


# ── Accounts ─────────────────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/financial-accounts", response_model=list[FinancialAccountResponse])
def list_accounts(
    matter_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[FinancialAccountResponse]:
    """List the accounts on a matter — the financial section of the inventory."""
    records = FinancialAccountRepository(manager).get_by_matter(matter_id)
    return [FinancialAccountResponse(**r.model_dump()) for r in records]


@router.get("/matters/{matter_id}/undisclosed-accounts",
            response_model=list[UndisclosedAccountResponse])
def list_undisclosed_accounts(
    matter_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[UndisclosedAccountResponse]:
    """
    Accounts the produced transactions name but no produced statement covers.

    Read-only and derived on demand — nothing is stored, so the answer always
    reflects the accounts and statements as they stand right now. Adding the
    missing account to the matter makes it disappear from this list, which is
    the workflow: the list is the outstanding question, not a record.
    """
    found = account_discovery_service.undisclosed(manager, matter_id)
    return [UndisclosedAccountResponse(**entry) for entry in found]


@router.post("/matters/{matter_id}/undisclosed-accounts/export")
def export_undisclosed_accounts(
    matter_id: int,
    body: ExhibitExportRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """
    Export the referenced-but-not-produced list — the attachment to a motion.

    Same four formats and the same rules as the transaction export: ``csv`` is
    the clean extraction, the other three carry the case caption and the
    verification notice. The dagger marking an inferred institution travels with
    its footnote into every exhibit format.
    """
    matter = MatterRepository(manager).select_one(condition={"id": matter_id})
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    try:
        exhibit = account_discovery_service.build_exhibit(
            manager, matter, body.exhibit_name.strip() or "Accounts Referenced But Not Produced",
        )
        content, media_type, filename = exhibit_service.render(exhibit, body.format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — a renderer failure must not read as an empty export
        LOGGER.error("financial.export_undisclosed_accounts: matter=%s format=%s failed: %s",
                     matter_id, body.format, str(e))
        raise HTTPException(status_code=500, detail="Could not build the export") from e

    headers = {
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "X-Exhibit-Rows": str(len(exhibit.rows)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Exhibit-Rows, X-Exhibit-Warnings",
    }
    if exhibit.warnings:
        headers["X-Exhibit-Warnings"] = quote(" | ".join(exhibit.warnings))

    return Response(content=content, media_type=media_type, headers=headers)


@router.patch("/financial-accounts/{account_id}", response_model=FinancialAccountResponse)
def update_account(
    account_id: int,
    body: FinancialAccountUpdateRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> FinancialAccountResponse:
    """
    Correct or characterize an account.

    Extraction fills in institution and number; characterization, ownership,
    and purpose are attorney judgments and are only ever set here.
    """
    repo = FinancialAccountRepository(manager)
    if repo.select_one(condition={"id": account_id}) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    # exclude_unset, not exclude_none: clearing a note or a characterization back
    # to null is a real edit, and exclude_none would silently drop it.
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    return FinancialAccountResponse(**repo.update(account_id, updates).model_dump())


@router.get("/financial-accounts/{account_id}/merge-preview/{target_account_id}",
            response_model=AccountMergePreview)
def preview_account_merge(
    account_id: int,
    target_account_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> AccountMergePreview:
    """
    Report what merging one account into another would do.

    Accounts split in two for a mundane reason: many statements print the
    institution only in the letterhead graphic, so the first upload files an
    "Unknown institution" account, and correcting that name does not
    retroactively match the next upload.

    Merging moves evidence and drops a row, so it reports before it acts.
    """
    try:
        preview = statement_service.preview_merge(manager, account_id, target_account_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return AccountMergePreview(**preview)


@router.post("/financial-accounts/{account_id}/merge", response_model=AccountMergeResult)
def merge_accounts(
    account_id: int,
    body: AccountMergeRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> AccountMergeResult:
    """
    Move every statement off this account onto another, then delete this one.

    Transactions follow their statements automatically — migration 026 made
    that composite foreign key ``ON UPDATE CASCADE``.
    """
    try:
        result = statement_service.merge(manager, account_id, body.target_account_id, force=body.force)
    except ValueError as e:
        # 409: the request is well-formed, the state will not allow it.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return AccountMergeResult(
        statements_moved=result["statements_moved"],
        transactions_moved=result["transactions_moved"],
        target=FinancialAccountResponse(**result["target"].model_dump()),
    )



@router.get("/financial-accounts/{account_id}/delete-preview", response_model=AccountDeletePreview)
def preview_account_delete(
    account_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> AccountDeletePreview:
    """What deleting this account would take with it, and any reason to pause."""
    try:
        return AccountDeletePreview(**statement_service.preview_account_delete(manager, account_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/financial-accounts/{account_id}", response_model=AccountDeletePreview)
def delete_account(
    account_id: int,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> AccountDeletePreview:
    """
    Delete an account, its statements, and their transactions.

    For a statement imported into the wrong matter, or an account that only
    exists because an early extraction misread the institution and a clean copy
    was ingested afterwards.

    Hard, not soft: the PDFs are still in Storage, so a mistake costs a
    re-import, while a half-deleted account sitting in an inventory is worse
    than one that is gone.
    """
    try:
        result = statement_service.delete_account(manager, account_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    AuditLogger(manager).log(
        action="financial_account_deleted",
        entity_type="financial_accounts",
        entity_id=str(account_id),
        supabase_uid=request.state.supabase_uid,
        after_json=result,
    )
    return AccountDeletePreview(**result)


# ── Statements ───────────────────────────────────────────────────────────────

@router.get("/financial-accounts/{account_id}/statements", response_model=list[StatementResponse])
def list_statements(
    account_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[StatementResponse]:
    """An account's statements, oldest period first."""
    records = FinancialAccountStatementRepository(manager).get_by_account(account_id)
    return [_statement_response(r) for r in records]


@router.get("/matters/{matter_id}/statements/exceptions", response_model=list[StatementResponse])
def list_exceptions(
    matter_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[StatementResponse]:
    """
    The exceptions queue: statements that did not clear on their own.

    A statement reaches this list because it failed to reconcile, printed no
    balances to check against, matched no account, or carried a warning from
    extraction. Everything else was accepted without review.
    """
    records = FinancialAccountStatementRepository(manager).needing_review(matter_id)
    return [_statement_response(r) for r in records]


@router.patch("/statements/{statement_id}/review", response_model=StatementReviewResponse)
def review_statement(
    statement_id: int,
    body: StatementReviewRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> StatementReviewResponse:
    """
    Clear a statement out of the exceptions queue.

    ``accepted`` keeps it, unreconciled or not — an exhibit can still footnote
    the discrepancy.

    ``rejected`` **deletes** it, along with its transactions and, when nothing
    of value would go with it, the account it created. A rejected extraction is
    not evidence held back, it is data that was read wrong; leaving it filtered
    out but present means nobody can act on it and the empty account it opened
    sits in the inventory forever.
    """
    repo = FinancialAccountStatementRepository(manager)
    record = repo.select_one(condition={"id": statement_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    if body.review_status not in (StatementReviewStatus.accepted, StatementReviewStatus.rejected):
        raise HTTPException(status_code=422, detail="Review sets 'accepted' or 'rejected'")

    if body.review_status == StatementReviewStatus.rejected:
        try:
            result = statement_service.reject_statement(manager, statement_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        # Deleting evidence is exactly what §13 wants a second, immutable record of.
        AuditLogger(manager).log(
            action="financial_statement_rejected",
            entity_type="financial_account_statements",
            entity_id=str(statement_id),
            supabase_uid=request.state.supabase_uid,
            after_json=result,
        )
        return StatementReviewResponse(discarded=StatementRejectResult(**result))

    updated = repo.update(statement_id, {"review_status": StatementReviewStatus.accepted.value})
    LOGGER.info("financial.review_statement: statement=%s accepted", statement_id)
    return StatementReviewResponse(statement=_statement_response(updated))



@router.delete("/statements/{statement_id}", response_model=StatementRejectResult)
def delete_statement(
    statement_id: int,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> StatementRejectResult:
    """
    Delete a statement, its transactions, and an account left empty by it.

    Identical to rejecting from the exceptions queue — the same discard, reached
    from the statement itself. That matters because a statement can look fine on
    ingest and only later turn out to be a mess: pages scanned out of order,
    pages missing, no Bates numbers to catch it by. Reject only ever reached the
    ones that failed a check.

    The source PDF stays in Storage, so backing an import out and running it
    again costs the extraction, not the evidence.
    """
    try:
        result = statement_service.reject_statement(manager, statement_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    AuditLogger(manager).log(
        action="financial_statement_deleted",
        entity_type="financial_account_statements",
        entity_id=str(statement_id),
        supabase_uid=request.state.supabase_uid,
        after_json=result,
    )
    return StatementRejectResult(**result)


# ── Transactions ─────────────────────────────────────────────────────────────

@router.get("/statements/{statement_id}/transactions", response_model=list[TransactionResponse])
def list_statement_transactions(
    statement_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[TransactionResponse]:
    """One statement's lines, in printed order."""
    records = FinancialAccountTransactionRepository(manager).get_by_statement(statement_id)
    return [TransactionResponse(**r.model_dump()) for r in records]


@router.get("/financial-accounts/{account_id}/transactions", response_model=list[TransactionResponse])
def list_account_transactions(
    account_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[TransactionResponse]:
    """
    An account's whole history in date order.

    This is the query behind the waste and reimbursement exhibits.
    """
    records = FinancialAccountTransactionRepository(manager).get_by_account(account_id)
    return [TransactionResponse(**r.model_dump()) for r in records]



@router.patch("/transactions/{transaction_id}", response_model=TransactionCorrectionResponse)
def correct_transaction(
    transaction_id: int,
    body: TransactionUpdateRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> TransactionCorrectionResponse:
    """
    Correct a value on an ingested line.

    The change is recorded on the line itself as a MANUAL_CORRECTION flag
    naming the field, both values, and who made it. The corrected figure ends up
    in an exhibit, and the first question on cross is where it came from.

    Correcting an amount re-reconciles the statement, which is the point of
    allowing the edit: an unreconciled statement is usually one misread figure.
    """
    staff, name = _staff_and_name(request, manager)
    updates = body.model_dump(exclude_unset=True)
    reason = updates.pop("reason", None)
    try:
        transaction, statement = statement_service.correct_transaction(
            manager, transaction_id, updates, staff.id, name, reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Editing evidence is exactly the kind of act §13 wants a second record of,
    # in a table that cannot be updated or deleted.
    AuditLogger(manager).log(
        action="financial_transaction_corrected",
        entity_type="financial_account_transactions",
        entity_id=str(transaction_id),
        supabase_uid=request.state.supabase_uid,
        after_json={"fields": sorted(updates), "reason": reason},
    )
    return TransactionCorrectionResponse(
        transaction=TransactionResponse(**transaction.model_dump()),
        statement=_statement_response(statement) if statement is not None else None,
    )


@router.post("/transactions/{transaction_id}/delete", response_model=TransactionCorrectionResponse)
def delete_transaction(
    transaction_id: int,
    body: TransactionDeleteRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> TransactionCorrectionResponse:
    """
    Drop a line from its statement.

    Hidden everywhere and excluded from every total, but kept: removing a line
    asserts it is not printed on the document, and that assertion reaches an
    exhibit. The statement comes back re-reconciled — if the line really was
    invented by extraction, the balance ties better without it.
    """
    staff, name = _staff_and_name(request, manager)
    try:
        transaction, statement = statement_service.delete_transaction(
            manager, transaction_id, staff.id, name, body.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    AuditLogger(manager).log(
        action="financial_transaction_deleted",
        entity_type="financial_account_transactions",
        entity_id=str(transaction_id),
        supabase_uid=request.state.supabase_uid,
        after_json={"reason": body.reason},
    )
    return TransactionCorrectionResponse(
        transaction=TransactionResponse(**transaction.model_dump()),
        statement=_statement_response(statement) if statement is not None else None,
    )


@router.post("/transactions/{transaction_id}/restore", response_model=TransactionCorrectionResponse)
def restore_transaction(
    transaction_id: int,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> TransactionCorrectionResponse:
    """Put a dropped line back, and re-reconcile the statement with it."""
    staff, name = _staff_and_name(request, manager)
    try:
        transaction, statement = statement_service.restore_transaction(
            manager, transaction_id, staff.id, name,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    AuditLogger(manager).log(
        action="financial_transaction_restored",
        entity_type="financial_account_transactions",
        entity_id=str(transaction_id),
        supabase_uid=request.state.supabase_uid,
    )
    return TransactionCorrectionResponse(
        transaction=TransactionResponse(**transaction.model_dump()),
        statement=_statement_response(statement) if statement is not None else None,
    )



# ── Categories (the firm-wide chart of accounts) ─────────────────────────────

def _decorate(categories: list) -> list[TransactionCategoryResponse]:
    """
    Add depth and a full path to each node.

    A leaf name on its own is ambiguous — "Gas" is both a utility and a car
    expense — so the picker shows the whole path. Computed on read rather than
    stored: a stored path goes stale the moment a category is reparented.
    """
    by_id = {c.id: c for c in categories}
    out: list[TransactionCategoryResponse] = []
    for category in categories:
        names: list[str] = []
        depth = 0
        current = category
        seen: set[int] = set()
        while current is not None and current.id not in seen:
            seen.add(current.id)
            names.append(current.description)
            current = by_id.get(current.parent_id) if current.parent_id else None
            if current is not None:
                depth += 1
        out.append(TransactionCategoryResponse(
            **category.model_dump(), depth=depth, path=" > ".join(reversed(names)),
        ))
    return out


@router.get("/transaction-categories", response_model=list[TransactionCategoryResponse])
def list_categories(
    include_inactive: bool = False,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[TransactionCategoryResponse]:
    """
    The firm-wide category tree in display order.

    Not matter-scoped on purpose: a Financial Information Statement is only
    comparable across cases when every case buckets to the same chart.
    """
    return _decorate(TransactionCategoryRepository(manager).get_ordered(include_inactive=include_inactive))


@router.post("/transaction-categories", response_model=TransactionCategoryResponse, status_code=201)
def create_category(
    body: TransactionCategoryWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["admin", "attorney"])),
) -> TransactionCategoryResponse:
    """Add a category. Firm-wide, so it changes every matter's chart."""
    if not (body.description or "").strip():
        raise HTTPException(status_code=422, detail="A category needs a description")
    repo = TransactionCategoryRepository(manager)
    if body.parent_id is not None and repo.select_one(condition={"id": body.parent_id}) is None:
        raise HTTPException(status_code=422, detail="No such parent category")
    try:
        created = repo.insert(TransactionCategory(
            description=body.description.strip(),
            parent_id=body.parent_id,
            display_order=body.display_order or 0,
            include_in_fis=True if body.include_in_fis is None else body.include_in_fis,
        ).model_dump())
    except KeyError as e:
        raise HTTPException(status_code=409, detail="A sibling category already has that name") from e
    return next(c for c in _decorate(repo.get_all(include_inactive=True)) if c.id == created.id)


@router.patch("/transaction-categories/{category_id}", response_model=TransactionCategoryResponse)
def update_category(
    category_id: int,
    body: TransactionCategoryWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["admin", "attorney"])),
) -> TransactionCategoryResponse:
    """Amend a category. Reparenting it under its own descendant is refused."""
    repo = TransactionCategoryRepository(manager)
    if repo.select_one(condition={"id": category_id}) is None:
        raise HTTPException(status_code=404, detail="Category not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    if updates.get("parent_id") is not None:
        if updates["parent_id"] == category_id:
            raise HTTPException(status_code=422, detail="A category cannot be its own parent")
        # Reparenting under a descendant makes a cycle: the branch detaches from
        # the tree, and every walk over it either loops or loses those rows.
        if updates["parent_id"] in repo.expand([category_id]):
            raise HTTPException(status_code=422, detail="That would put the category inside its own branch")

    updated = repo.update(category_id, updates)
    return next(c for c in _decorate(repo.get_all(include_inactive=True)) if c.id == updated.id)


@router.delete("/transaction-categories/{category_id}", status_code=204)
def delete_category(
    category_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> None:
    """
    Remove a category that nothing uses.

    Refused while transactions are filed under it or children hang off it.
    Deactivate instead — quietly un-filing evidence because someone tidied the
    chart is how an FIS loses a line with nobody noticing.
    """
    repo = TransactionCategoryRepository(manager)
    if repo.select_one(condition={"id": category_id}) is None:
        raise HTTPException(status_code=404, detail="Category not found")
    in_use = repo.in_use(category_id)
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="%d transaction(s) are filed under this category. Deactivate it instead." % in_use,
        )
    if any(c.parent_id == category_id for c in repo.get_all(include_inactive=True)):
        raise HTTPException(status_code=409, detail="This category still has subcategories")
    repo.delete(category_id)


# ── Tags ─────────────────────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/transaction-tags", response_model=list[TransactionTagResponse])
def list_tags(
    matter_id: int,
    include_inactive: bool = False,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[TransactionTagResponse]:
    """
    Every tag offered on this matter: the firm-wide layer plus the case's own.

    Usage counts come back with them — the number of lines behind a tag is the
    size of the exhibit it would produce.
    """
    repo = TransactionTagRepository(manager)
    tags = repo.available_for_matter(matter_id, include_inactive=include_inactive)
    return [TransactionTagResponse(**t.model_dump(), usage_count=repo.in_use(t.id)) for t in tags]


@router.post("/matters/{matter_id}/transaction-tags",
             response_model=TransactionTagResponse, status_code=201)
def create_matter_tag(
    matter_id: int,
    body: TransactionTagWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> TransactionTagResponse:
    """
    Add a tag scoped to this matter, e.g. "Waste: Sister's Wedding".

    Matter-scoped because the label states a theory about this case; it means
    nothing on anyone else's and should not clutter their picker.
    """
    if not (body.label or "").strip():
        raise HTTPException(status_code=422, detail="A tag needs a label")
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    try:
        created = TransactionTagRepository(manager).insert(TransactionTag(
            matter_id=matter_id,
            label=body.label.strip(),
            description=body.description,
            color=body.color,
            display_order=body.display_order or 500,
        ).model_dump())
    except KeyError as e:
        raise HTTPException(status_code=409, detail="This matter already has a tag with that label") from e
    return TransactionTagResponse(**created.model_dump(), usage_count=0)


@router.post("/transaction-tags", response_model=TransactionTagResponse, status_code=201)
def create_firm_tag(
    body: TransactionTagWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["admin", "attorney"])),
) -> TransactionTagResponse:
    """Add a firm-wide tag, offered on every matter."""
    if not (body.label or "").strip():
        raise HTTPException(status_code=422, detail="A tag needs a label")
    try:
        created = TransactionTagRepository(manager).insert(TransactionTag(
            matter_id=None,
            label=body.label.strip(),
            description=body.description,
            color=body.color,
            display_order=body.display_order or 500,
        ).model_dump())
    except KeyError as e:
        raise HTTPException(status_code=409, detail="A firm-wide tag with that label exists") from e
    return TransactionTagResponse(**created.model_dump(), usage_count=0)


@router.patch("/transaction-tags/{tag_id}", response_model=TransactionTagResponse)
def update_tag(
    tag_id: int,
    body: TransactionTagWriteRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> TransactionTagResponse:
    """Rename or restyle a tag. Its links to transactions are untouched."""
    repo = TransactionTagRepository(manager)
    if repo.select_one(condition={"id": tag_id}) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    updated = repo.update(tag_id, updates)
    return TransactionTagResponse(**updated.model_dump(), usage_count=repo.in_use(tag_id))


@router.delete("/transaction-tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: int,
    force: bool = False,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> None:
    """
    Delete a tag.

    Refused while it is in use unless ``force=true``, because the delete
    cascades: removing a tag applied to three hundred lines dissolves an exhibit
    without a trace. The count in the error is what the caller needs to decide.
    """
    repo = TransactionTagRepository(manager)
    if repo.select_one(condition={"id": tag_id}) is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    in_use = repo.in_use(tag_id)
    if in_use and not force:
        raise HTTPException(
            status_code=409,
            detail="%d transaction(s) carry this tag. Deactivate it, or delete with force=true." % in_use,
        )
    LOGGER.info("financial.delete_tag: tag=%s in_use=%s forced=%s", tag_id, in_use, force)
    repo.delete(tag_id)


# ── Search and bulk classification ───────────────────────────────────────────

@router.post("/matters/{matter_id}/transactions/search", response_model=TransactionSearchResponse)
def search_transactions(
    matter_id: int,
    body: TransactionSearchRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> TransactionSearchResponse:
    """
    Filter a matter's transactions by account, date, category, tag, and text.

    The matter bounds every search: transactions carry no matter_id, so the
    service resolves the matter's accounts first and intersects any account
    filter against them.
    """
    result = transaction_search_service.search(
        manager=manager,
        matter_id=matter_id,
        account_ids=body.account_ids,
        date_from=body.date_from,
        date_to=body.date_to,
        category_ids=body.category_ids,
        include_subcategories=body.include_subcategories,
        uncategorized=body.uncategorized,
        tag_ids=body.tag_ids,
        tag_match_all=body.tag_match_all,
        untagged=body.untagged,
        text=body.text,
        check_number=body.check_number,
        checks_only=body.checks_only,
        include_deleted=body.include_deleted,
        limit=body.limit,
        offset=body.offset,
    )
    return TransactionSearchResponse(**result)


@router.post("/matters/{matter_id}/transactions/review", response_model=BulkResultResponse)
def review_transactions(
    matter_id: int,
    body: ReviewRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> BulkResultResponse:
    """
    Confirm automatic assignments a person has checked and agreed with.

    Leaves the category alone. Agreeing with a rule is not the same act as
    filing a line, and rewriting the source would erase the fact that the rule
    got it right — which is the evidence that the rules are working.
    """
    try:
        changed = transaction_search_service.mark_reviewed(
            manager, matter_id, body.transaction_ids, _staff_id(request, manager),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return BulkResultResponse(updated=changed)


@router.post("/matters/{matter_id}/transactions/export")
def export_transactions(
    matter_id: int,
    body: TransactionExportRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """
    Export the current query as a CSV extraction or a court exhibit.

    The export covers **every** matching line, not the page on screen — a
    summary that stopped at the page size would look complete and be wrong. It
    is a synchronous request because the work is a database read and a document
    write: no LLM, no OCR, so haproxy's limit is not in play (§11a).

    ``csv`` is the clean extraction — header row and data, nothing else.
    ``md``, ``docx``, and ``pdf`` are full exhibits carrying the case caption
    and the Rule 1006 verification notice.

    Anything the caption needed and the matter could not supply is printed as a
    blank and named in the ``X-Exhibit-Warnings`` header, so the UI can say so
    rather than letting a blank reach a filing unnoticed.
    """
    matter = MatterRepository(manager).select_one(condition={"id": matter_id})
    if matter is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    criteria = body.model_dump(exclude={"format", "exhibit_name"})
    try:
        exhibit = transaction_search_service.build_exhibit(
            manager, matter, body.exhibit_name.strip() or "Financial Summary", criteria,
        )
        content, media_type, filename = exhibit_service.render(exhibit, body.format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — a renderer failure must not read as an empty export
        LOGGER.error("financial.export_transactions: matter=%s format=%s failed: %s",
                     matter_id, body.format, str(e))
        raise HTTPException(status_code=500, detail="Could not build the export") from e

    headers = {
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        # Read by the browser, which cannot see a JSON body on a file download.
        "X-Exhibit-Rows": str(len(exhibit.rows)),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Exhibit-Rows, X-Exhibit-Warnings",
    }
    if exhibit.warnings:
        # Header values are latin-1 on the wire; a caption warning quoting a
        # matter name with a curly apostrophe would otherwise fail the response.
        headers["X-Exhibit-Warnings"] = quote(" | ".join(exhibit.warnings))

    return Response(content=content, media_type=media_type, headers=headers)


@router.post("/matters/{matter_id}/transactions/categorize", response_model=BulkResultResponse)
def categorize_transactions(
    matter_id: int,
    body: BulkCategorizeRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> BulkResultResponse:
    """File a set of transactions under one category, or clear it."""
    try:
        changed = transaction_search_service.set_category(
            manager, matter_id, body.transaction_ids, body.category_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return BulkResultResponse(changed=changed)


@router.post("/matters/{matter_id}/transactions/tag", response_model=BulkResultResponse)
def tag_transactions(
    matter_id: int,
    body: BulkTagRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> BulkResultResponse:
    """
    Apply or remove one tag across a set of transactions.

    Who applied it is recorded on every link — tagging is an attorney judgment,
    and it gets cross-examined.
    """
    try:
        changed = transaction_search_service.apply_tag(
            manager, matter_id, body.transaction_ids, body.tag_id,
            _staff_id(request, manager), remove=body.remove,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return BulkResultResponse(changed=changed)


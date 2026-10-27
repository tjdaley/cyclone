"""
app/routers/bench.py - The splitting bench: one received PDF becomes N documents.

Thin, like every router here. Why a person does this rather than a model, and
every decision about the screen, lives in ``splitting_bench_service`` and in
``docs/specs/document-labeling/SPEC_splitting_bench.md``.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from db.repositories.document import DocumentSourcePageRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.bench import (
    BenchGridResponse,
    BenchSessionResponse,
    BundleRequest,
    BundleResponse,
    BundleSummary,
    FinishResponse,
)
from schemas.financial import StatementIngestJobResponse
from services.document_processor_service import (
    ProcessorError, document_processor_service,
)
from services.splitting_bench_service import BenchError, splitting_bench_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["bench"])

_STAFF_ROLES = ["attorney", "admin", "paralegal"]

# The grid shows twenty at a time; the cap is generous so a client can prefetch
# the next screen without a second round trip, and bounded so nobody asks for
# 700 pages of badges in one response.
_MAX_GRID = 60


class IngestAsStatementRequest(BaseModel):
    """Options for reading a document as a statement."""

    bates_prefix: Optional[str] = Field(
        default=None,
        description="Stamp prefix, only for a production carrying two competing series. "
                    "The stamp is normally found by pattern")


def _staff_id(request: Request, manager) -> Optional[int]:
    uid = getattr(request.state, "supabase_uid", None)
    if not uid:
        return None
    staff = StaffRepository(manager).select_one(condition={"supabase_uid": uid})
    return staff.id if staff else None


@router.post("/source-documents/{source_document_id}/bench",
             response_model=BenchSessionResponse)
def open_bench(
    source_document_id: int,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> BenchSessionResponse:
    """
    Open or resume work on a received file.

    There is no session row: the grid is derived from the file and the page
    ranges existing documents already claim, so resuming is free — close the
    browser halfway through 700 pages and come back to the same grid.

    Reports the size of the job, which is what makes cancelling a real choice
    rather than a trap: an oversized pile routes here instead of being refused.
    """
    try:
        session = splitting_bench_service.open_session(
            manager, source_document_id,
            supabase_uid=getattr(request.state, "supabase_uid", None))
    except BenchError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # Claimed AFTER the session is described, so a held pile still reports its
    # size: somebody who cannot take it can still see what it is.
    #
    # The claim NAMES its owner. This endpoint is re-called after every bundle,
    # so an anonymous claim reported the operator as somebody else from their
    # second bundle onward.
    uid = getattr(request.state, "supabase_uid", None) or "unknown"
    ours, holder = splitting_bench_service.take(source_document_id, uid)
    return BenchSessionResponse(
        **session,
        held_by_other=not ours,
        bundles=splitting_bench_service.bundles(manager, source_document_id),
    )


@router.get("/source-documents/{source_document_id}/bench/bundles",
            response_model=list[BundleSummary])
def bench_bundles(
    source_document_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[BundleSummary]:
    """
    The documents already split off this file, and which pages are in each.

    Server state, read rather than remembered. A client-side list is lost the
    moment somebody closes the page or finishes, which leaves them told there
    are four documents with no way to see what is in them — and guessing on this
    screen creates duplicates.
    """
    try:
        return [BundleSummary(**b)
                for b in splitting_bench_service.bundles(manager, source_document_id)]
    except BenchError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/source-documents/{source_document_id}/bench/pages",
            response_model=BenchGridResponse)
def bench_pages(
    source_document_id: int,
    offset: int = Query(0, ge=0, description="First page to return, 0-based index"),
    limit: int = Query(20, ge=1, le=_MAX_GRID, description="How many pages"),
    include_claimed: bool = Query(
        False,
        description="Show pages already bundled. Off by default, so the grid empties as work "
                    "proceeds and is its own progress indicator. On, it is the only way to "
                    "reach a page that belongs to two documents — which a combined statement "
                    "requires, since one sheet can carry one account's close and the next "
                    "account's opening"),
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> BenchGridResponse:
    """One screen of the grid, from the stored badges rather than the PDF."""
    try:
        return BenchGridResponse(**splitting_bench_service.grid(
            manager, source_document_id, offset=offset, limit=limit,
            include_claimed=include_claimed))
    except BenchError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/source-documents/{source_document_id}/bench/pages/{page_number}/thumbnail")
def bench_thumbnail(
    source_document_id: int,
    page_number: int,
    w: int = Query(450, ge=80, le=1600, description="Target width in pixels"),
    fmt: str = Query("jpeg", pattern="^(jpeg|png)$"),
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """
    One rendered page, large enough to recognise a letterhead and a date.

    Not the OCR rendering, which is greyscale at contrast 2.0 and tuned for a
    model reading characters — those settings make a page harder for a person to
    recognise, which is the whole job here.

    Cached client-side for an hour: a page of a received file is immutable, and
    the grid re-requests the same twenty thumbnails every time somebody scrolls
    back.
    """
    try:
        image = splitting_bench_service.thumbnail(
            manager, source_document_id, page_number, width=w, fmt=fmt)
    except BenchError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if image is None:
        raise HTTPException(
            status_code=404,
            detail="That page could not be rendered. The original may have been purged "
                   "from storage, or the page number may be past the end of the file.")
    return Response(content=image, media_type="image/%s" % fmt,
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/source-documents/{source_document_id}/bench/bundles",
             response_model=BundleResponse, status_code=201)
def bundle_pages(
    source_document_id: int,
    payload: BundleRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> BundleResponse:
    """
    Make the selected pages one document.

    Non-adjacent pages are ordinary, not a special case: a scanned pile holds
    documents interrupted mid-way and resumed later, and selecting them here is
    the recorded assertion that they are one document.
    """
    try:
        document = splitting_bench_service.bundle(
            manager, source_document_id, payload.pages,
            supabase_uid=getattr(request.state, "supabase_uid", None))
        extents = DocumentSourcePageRepository(manager).get_by_document(document.id)
        session = splitting_bench_service.open_session(manager, source_document_id)
    except BenchError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return BundleResponse(
        document_id=document.id,
        ranges=["%d-%d" % (e.page_first, e.page_last) for e in extents],
        page_count=sum(e.page_last - e.page_first + 1 for e in extents),
        storage_path=document.storage_path,
        pages_unclaimed=session["pages_unclaimed"],
    )


@router.delete("/bench/bundles/{document_id}", status_code=204)
def undo_bundle(
    document_id: int,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """
    Put a bundle's pages back on the grid.

    A hard delete, matching the rule for a statement or an account: the
    as-produced original is the undo, so soft state here would infect the grid,
    the unclaimed count and the index for no gain.

    Undo is load-bearing rather than a convenience — bundled pages vanish from
    the grid, which is what makes the grid self-tracking and also what makes a
    mis-click unrecoverable by eye.
    """
    try:
        splitting_bench_service.undo(
            manager, document_id,
            supabase_uid=getattr(request.state, "supabase_uid", None))
    except BenchError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return Response(status_code=204)


@router.post("/source-documents/{source_document_id}/bench/finish",
             response_model=FinishResponse)
def finish_bench(
    source_document_id: int,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> FinishResponse:
    """
    Declare the pile split, sweeping what is left.

    Unclaimed pages become one ``blank_or_separator`` document. They were
    produced and they carry Bates numbers, so leaving them out would make the
    production index short — the one thing that index must not be.
    """
    try:
        return FinishResponse(**splitting_bench_service.finish(
            manager, source_document_id,
            supabase_uid=getattr(request.state, "supabase_uid", None)))
    except BenchError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/documents/{document_id}/ingest-as-statement",
             response_model=StatementIngestJobResponse, status_code=202)
def ingest_as_statement(
    document_id: int,
    request: Request,
    payload: Optional[IngestAsStatementRequest] = None,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> StatementIngestJobResponse:
    """
    Read a split document as an account statement.

    The one route out of the bench into a processor, and for now the route is
    chosen by a person: the classifier that would choose it needs a taxonomy
    nobody has finalised. Until this existed, splitting a statement pile
    stranded the pieces — which is what kept the oversized upload refusing
    rather than routing.

    202 with a job to poll, like every other statement ingest. The statement it
    produces is linked back to this document, unless the document holds several
    statements — a combined bank package is one document with three accounts in
    it, and picking one of them would make the index cite one account's period
    for all three.
    """
    staff_id = _staff_id(request, manager)
    if staff_id is None:
        raise HTTPException(
            status_code=403,
            detail="Your login is not correlated to a staff record, so work cannot be "
                   "attributed to you.")
    try:
        job = document_processor_service.send_to_statement_ingest(
            manager, document_id, staff_id,
            bates_prefix=(payload.bates_prefix or None) if payload else None)
    except ProcessorError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return StatementIngestJobResponse(id=job.id, status=job.status.value)


@router.post("/source-documents/{source_document_id}/bench/leave", status_code=204)
def leave_bench(
    source_document_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> Response:
    """
    Give the pile up without finishing it.

    This is cancel. The unsplit file is **kept** — it was served on us, which is
    the whole argument for holding an as-produced record, and deleting it
    because somebody declined a task would discard evidence. It sits in a
    received-but-not-split state until anybody comes back to it.
    """
    splitting_bench_service.leave(source_document_id)
    return Response(status_code=204)

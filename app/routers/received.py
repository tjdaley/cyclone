"""
app/routers/received.py - Files as they arrived, and where each one should go.

Thin, like every router here. The routing thresholds and the measurements behind
them are in ``source_document_service``.
"""
from typing import Any, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)

from db.repositories.document import DocumentRepository, SourceDocumentRepository
from db.repositories.matter import MatterRepository
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.received import (
    ReceivedDocumentResponse,
    ReceivedUploadResponse,
    RegisterUploadRequest,
    UploadUrlRequest,
    UploadUrlResponse,
)
from services.job_service import job_service
from services.source_document_service import ROUTE_OCR, source_document_service
from services.splitting_bench_service import splitting_bench_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["received-documents"])

_STAFF_ROLES = ["attorney", "admin", "paralegal"]

# PDF only, and the restriction is load-bearing rather than incidental — see
# the note on image transformation in `storage_service`. Accepted here as a
# media type the CLIENT claims, which is worth saying plainly: it stops an
# ordinary mistake, not a determined one. What actually arrives is settled when
# the survey job opens the file.
_ACCEPTED = ("application/pdf", "application/octet-stream")


def _reject_non_pdf(media_type: Optional[str]) -> None:
    """Refuse anything but a PDF, on the direct-upload path too.

    The multipart routes have gated this since they were written; the signed-URL
    path did not, so "we only upload PDFs" held by convention on the one route
    where nothing enforced it. That matters more than it looks: the conclusion
    that Supabase's image transformation cannot touch us rests on it.
    """
    if media_type not in _ACCEPTED:
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are accepted here for now. A photograph or a TIFF has "
                   "to be converted first, and that conversion is not built yet.")


def _staff_id(request: Request, manager: Any) -> Optional[int]:
    uid = getattr(request.state, "supabase_uid", None)
    if not uid:
        return None
    staff = StaffRepository(manager).select_one(condition={"supabase_uid": uid})
    return staff.id if staff else None


@router.post("/matters/{matter_id}/received-documents/upload-url",
             response_model=UploadUrlResponse)
def upload_url(
    matter_id: int,
    payload: UploadUrlRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> UploadUrlResponse:
    """
    Where to PUT a received file, so it never passes through this application.

    A 74 MB upload sent through here moves twice inside one request — browser to
    the API, API to Storage — and haproxy cut that with a 504 while everything
    was still working. A signed URL takes the application out of the path
    entirely, which also removes the memory ceiling (every upload route reads
    the whole body into RAM) and the nginx body-size limit along with it.

    Answers immediately with the existing record when this matter already holds
    these bytes. On a large production that is the difference between instant
    and minutes, and it is only possible because the key is the content rather
    than the filename.
    """
    _reject_non_pdf(payload.media_type)
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    try:
        return UploadUrlResponse(**source_document_service.prepare_upload(
            manager, matter_id, payload.content_sha256,
            payload.filename.strip() or "untitled", payload.media_type))
    except Exception as e:  # noqa: BLE001
        LOGGER.error("received.upload_url: matter=%s failed: %s", matter_id, str(e))
        raise HTTPException(
            status_code=502,
            detail="Could not prepare the upload. Storage may be unavailable.") from e


@router.post("/matters/{matter_id}/received-documents/register",
             response_model=ReceivedUploadResponse, status_code=201)
def register_upload(
    matter_id: int,
    payload: RegisterUploadRequest,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> ReceivedUploadResponse:
    """
    Record a file the browser has already put into storage, and queue its survey.

    **No verdict comes back yet.** Deciding what the file is means reading every
    page of it, and on a large production that is a download this request must
    not wait for — the same rule that moved the upload itself off this path.
    The survey runs as a job; the list shows the verdict when it lands.
    """
    _reject_non_pdf(payload.media_type)
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    staff_id = _staff_id(request, manager)
    if staff_id is None:
        # jobs.requested_by_staff_id is NOT NULL with a foreign key, so there is
        # no anonymous survey to queue. Refusing here beats recording a file
        # whose survey could never run and whose verdict would never arrive.
        raise HTTPException(
            status_code=403,
            detail="Your login is not correlated to a staff record, so work cannot be "
                   "attributed to you. Ask an administrator to correlate it.")
    try:
        record, created = source_document_service.register(
            manager, matter_id, payload.path, payload.content_sha256,
            original_filename=payload.filename.strip() or "untitled",
            byte_count=payload.byte_count,
            media_type=payload.media_type,
            received_by_staff_id=staff_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if created:
        try:
            job_service.enqueue_document_survey(
                manager, staff_id, matter_id, record.id, record.storage_path)
        except Exception as e:  # noqa: BLE001 — the file is safe either way
            LOGGER.error("received.register_upload: could not queue the survey: %s", str(e))

    return ReceivedUploadResponse(
        source_document_id=record.id,
        matter_id=matter_id,
        original_filename=record.original_filename,
        already_held=not created,
        byte_count=record.byte_count,
        **source_document_service.route_from_features(record.page_features),
    )


@router.post("/matters/{matter_id}/received-documents",
             response_model=ReceivedUploadResponse, status_code=201)
def receive_document(
    matter_id: int,
    request: Request,
    file: UploadFile = File(...),
    production_id: Optional[int] = Form(default=None),
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> ReceivedUploadResponse:
    """
    Record a file exactly as it arrived, and say what to do with it.

    **Nothing is refused for being large.** A pile past the automatic split's
    capacity is routed to the splitting bench instead, where a person can split
    it or decline — the file is kept either way, because it was served on us.
    That replaces a refusal which was right about the automatic path and had
    nothing to offer in its place.

    A file this matter already holds is returned rather than stored twice: a
    supplement re-serves documents, and keying on the bytes rather than the
    filename is what catches it.
    """
    if file.content_type not in _ACCEPTED:
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are accepted here for now. A photograph or a TIFF has "
                   "to be converted first, and that conversion is not built yet.")
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    try:
        record, created = source_document_service.receive(
            manager, matter_id, content,
            original_filename=(file.filename or "untitled").strip(),
            media_type=file.content_type,
            production_id=production_id,
            received_by_staff_id=_staff_id(request, manager),
        )
    except Exception as e:  # noqa: BLE001 — storage is the only failure here
        LOGGER.error("received.receive_document: could not store matter=%s: %s",
                     matter_id, str(e))
        raise HTTPException(
            status_code=502,
            detail="The file could not be stored, so nothing was recorded. Nothing is "
                   "half-saved — try again.") from e

    # The badges are computed and STORED here, not just the verdict. A file that
    # arrived this way is small by definition -- it fitted in a request -- so
    # reading it costs a fraction of a second, measured at 0.39s for 396 pages.
    #
    # Storing them is what stops this path leaving a file the bench cannot open.
    # Nothing else would ever compute them: the survey job is queued only by the
    # direct-upload path, and the bench no longer reads the file itself. Without
    # this, a small upload reached a grid with no pages in it, which said
    # "Every page is in a document" to somebody who had split nothing.
    features = splitting_bench_service.page_features(content)
    verdict = source_document_service.route_from_features(features)

    if created:
        SourceDocumentRepository(manager).update(
            record.id, {"page_features": features, "page_count": len(features)})

    return ReceivedUploadResponse(
        source_document_id=record.id,
        matter_id=matter_id,
        original_filename=record.original_filename,
        already_held=not created,
        byte_count=record.byte_count,
        **verdict,
    )


@router.get("/matters/{matter_id}/received-documents",
            response_model=list[ReceivedDocumentResponse])
def list_received(
    matter_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> list[ReceivedDocumentResponse]:
    """
    Every file this matter has received, and how far each has been split.

    This is the queue a cancelled pile sits in. An unsplit file is a legitimate
    state — it was served on us whether or not anybody has got to it — so the
    list reports what has been done rather than treating "not split" as an
    error.
    """
    sources = SourceDocumentRepository(manager).get_by_matter(matter_id)
    if not sources:
        return []

    docs = DocumentRepository(manager).get_by_matter(matter_id)
    split_count: dict[int, int] = {}
    for doc in docs:
        split_count[doc.source_document_id] = split_count.get(doc.source_document_id, 0) + 1

    return [
        ReceivedDocumentResponse(
            source_document_id=s.id,
            matter_id=s.matter_id,
            original_filename=s.original_filename,
            byte_count=s.byte_count,
            page_count=s.page_count,
            received_on=s.received_on,
            documents=split_count.get(s.id, 0),
            surveyed=bool(s.page_features),
        )
        for s in sources
    ]


@router.get("/received-documents/{source_document_id}/route",
            response_model=ReceivedUploadResponse)
def reroute(
    source_document_id: int,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> ReceivedUploadResponse:
    """
    Ask again what should be done with a file already on record.

    The answer changes: a pile routed to OCR has no text layer, and once it has
    one the same signals give a different verdict. That is the second half of
    ``ocr_first`` and the reason the route is recomputed rather than stored.
    """
    record = SourceDocumentRepository(manager).select_one(
        condition={"id": source_document_id})
    if record is None:
        raise HTTPException(status_code=404, detail="That received file is not on record")

    verdict = source_document_service.route_from_features(record.page_features)
    if verdict["route"] == ROUTE_OCR:
        LOGGER.info("received.reroute: id=%s still has no usable text layer",
                    source_document_id)
    return ReceivedUploadResponse(
        source_document_id=record.id,
        matter_id=record.matter_id,
        original_filename=record.original_filename,
        already_held=True,
        byte_count=record.byte_count,
        **verdict,
    )

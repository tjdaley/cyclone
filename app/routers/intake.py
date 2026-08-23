"""
app/routers/intake.py - Open a client and matter from a filed pleading.

Two steps, mirroring pleading ingestion: preview extracts and writes nothing,
then commit persists the attorney-reviewed result.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from typing import Any

from db.models.job import JobStatus
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.intake import (
    IntakeJobResponse,
    MatterIntakeCommitRequest,
    MatterIntakeCommitResponse,
    MatterIntakePreviewResponse,
)
from services.intake_service import intake_service
from services.job_service import job_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/matters/intake", tags=["intake"])


def _staff_id(request: Request, manager: Any) -> int:
    """Resolve the caller's staff row, which owns any job they start."""
    staff = StaffRepository(manager).get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")
    return staff.id


@router.post("/upload", response_model=IntakeJobResponse, status_code=202)
def upload_intake(
    request: Request,
    file: UploadFile = File(...),
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> IntakeJobResponse:
    """
    Accept a pleading for intake and return a job to poll. Persists no matter.

    202, not 200: reading a pleading means an LLM vision call per image-only
    page plus two more over the text. That is minutes on a scanned document —
    far longer than a reverse proxy will hold a connection — so the work moves
    to the worker and the caller polls ``GET /matters/intake/jobs/{id}``.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = file.file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    try:
        job = job_service.enqueue_matter_intake(manager, _staff_id(request, manager), pdf_bytes)
    except Exception as e:  # noqa: BLE001 — storage failure is the only path here
        LOGGER.error("intake.upload: could not queue extraction: %s", str(e))
        raise HTTPException(status_code=502, detail="Could not store the upload for processing") from e

    return IntakeJobResponse(id=job.id, status=job.status.value)


@router.get("/jobs/{job_id}", response_model=IntakeJobResponse)
def get_intake_job(
    job_id: str,
    request: Request,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> IntakeJobResponse:
    """
    Poll an extraction. Returns the case style once the status is 'succeeded'.

    Scoped to the staff member who uploaded: an extraction holds the full text
    of someone's pleading, so another user polling the id gets a 404.
    """
    job = job_service.get_for_staff(manager, job_id, _staff_id(request, manager))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    result = None
    if job.status == JobStatus.succeeded and job.result:
        try:
            result = MatterIntakePreviewResponse.model_validate(job.result)
        except Exception as e:  # noqa: BLE001 — a stored result from an older shape
            LOGGER.error("intake.get_job: job=%s result no longer parses: %s", job_id, str(e))
            return IntakeJobResponse(
                id=job.id, status=JobStatus.failed.value,
                error="This extraction was produced by an older version — please upload again.",
            )

    return IntakeJobResponse(id=job.id, status=job.status.value, result=result, error=job.error)


@router.post("/commit", response_model=MatterIntakeCommitResponse, status_code=201)
def commit_intake(
    request: Request,
    body: MatterIntakeCommitRequest,
    manager: Any = Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> MatterIntakeCommitResponse:
    """
    Create the client (if new), the matter, and the pleading behind it.

    Restricted to attorney/admin: this opens a file, which is a different act
    from adding data to a matter that already exists.
    """
    staff = StaffRepository(manager).get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")

    try:
        result = intake_service.commit(manager=manager, staff_id=staff.id, request=body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    LOGGER.info("intake.commit: client_id=%s matter_id=%s pleading_id=%s",
                result.client_id, result.matter_id, result.pleading_id)
    return result

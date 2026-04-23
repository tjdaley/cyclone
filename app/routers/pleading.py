"""
app/routers/pleading.py - Pleading ingestion and matter-level claim endpoints.
"""
import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from db.repositories.client import ClientRepository
from db.repositories.matter import MatterRepository
from db.repositories.pleading import (
    MatterChildRepository,
    MatterClaimRepository,
    MatterOpposingCounselRepository,
    MatterPleadingRepository,
    OpposingCounselRepository,
)
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.pleading import (
    MatterChildResponse,
    MatterClaimResponse,
    MatterClaimUpdateRequest,
    MatterOpposingCounselResponse,
    MatterPleadingResponse,
    MatterPleadingUpdateRequest,
    OpposingCounselResponse,
    OpposingCounselUpdateRequest,
    PleadingCommitRequest,
    PleadingCommitResponse,
    PleadingIngestPreviewResponse,
)
from services.pdf_service import pdf_service
from services.pleading_service import pleading_service
from services.storage_service import StorageService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["pleadings"])


# ── Pleading Ingestion ───────────────────────────────────────────────────────

@router.post("/pleadings/preview", response_model=PleadingIngestPreviewResponse)
def preview_pleading(
    file: UploadFile = File(...),
    matter_id: int = Form(...),
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> PleadingIngestPreviewResponse:
    """
    Upload a pleading PDF and return the LLM's extraction for attorney review.

    This endpoint does NOT persist anything. The frontend displays the preview,
    the attorney edits it, and then the reviewed version is sent to
    POST /pleadings/commit with the original PDF re-attached.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = file.file.read()
    try:
        raw_text = pdf_service.extract_text(pdf_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from the PDF")

    try:
        preview = pleading_service.preview_ingest(manager=manager, matter_id=matter_id, raw_text=raw_text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return preview


@router.post("/pleadings/commit", response_model=PleadingCommitResponse, status_code=201)
def commit_pleading(
    request: Request,
    body: PleadingCommitRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> PleadingCommitResponse:
    """
    Commit a reviewed pleading preview.

    Persists: pleading row, matter field updates, children, opposing counsel
    (new + updated), matter-counsel links, and claims.
    """
    # Resolve staff_id from JWT
    staff_repo = StaffRepository(manager)
    staff = staff_repo.get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")

    try:
        pleading_record, children_count, oc_count, claims_count = pleading_service.commit_ingest(
            manager=manager,
            staff_id=staff.id,
            request=body,
            pdf_bytes=None,  # PDF not re-uploaded in commit; preview step stored raw_text only
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return PleadingCommitResponse(
        pleading=MatterPleadingResponse(**pleading_record.model_dump()),
        children_created=children_count,
        opposing_counsel_linked=oc_count,
        claims_created=claims_count,
    )


# ── Pleading CRUD ────────────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/pleadings", response_model=list[MatterPleadingResponse])
def list_pleadings(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[MatterPleadingResponse]:
    """List all pleadings for a matter, oldest first."""
    repo = MatterPleadingRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [MatterPleadingResponse(**r.model_dump()) for r in records]


@router.patch("/pleadings/{pleading_id}", response_model=MatterPleadingResponse)
def update_pleading(
    pleading_id: int,
    body: MatterPleadingUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterPleadingResponse:
    """Update pleading metadata (title, dates, amendment chain)."""
    repo = MatterPleadingRepository(manager)
    if repo.select_one(condition={"id": pleading_id}) is None:
        raise HTTPException(status_code=404, detail="Pleading not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    record = repo.update(pleading_id, updates)
    return MatterPleadingResponse(**record.model_dump())


@router.get("/pleadings/{pleading_id}/download")
def download_pleading(
    pleading_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
):
    """Redirect to a signed URL for the original PDF."""
    repo = MatterPleadingRepository(manager)
    record = repo.select_one(condition={"id": pleading_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Pleading not found")
    if not record.storage_path:
        raise HTTPException(status_code=404, detail="Original PDF not available for this pleading")
    storage = StorageService(manager)
    url = storage.get_signed_url(record.storage_path, expires_in=300)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to generate signed URL")
    return RedirectResponse(url=url)


# ── Claims CRUD ──────────────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/claims", response_model=list[MatterClaimResponse])
def list_claims(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[MatterClaimResponse]:
    """List all claims/defenses for a matter."""
    repo = MatterClaimRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [MatterClaimResponse(**r.model_dump()) for r in records]


@router.patch("/claims/{claim_id}", response_model=MatterClaimResponse)
def update_claim(
    claim_id: int,
    body: MatterClaimUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterClaimResponse:
    """Update a claim/defense."""
    repo = MatterClaimRepository(manager)
    if repo.select_one(condition={"id": claim_id}) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    record = repo.update(claim_id, updates)
    return MatterClaimResponse(**record.model_dump())


@router.delete("/claims/{claim_id}", status_code=204)
def delete_claim(
    claim_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
):
    """Delete a claim/defense."""
    repo = MatterClaimRepository(manager)
    if repo.select_one(condition={"id": claim_id}) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    repo.delete(claim_id)


# ── Matter Children CRUD ─────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/children", response_model=list[MatterChildResponse])
def list_children(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[MatterChildResponse]:
    """List all children for a matter."""
    repo = MatterChildRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [MatterChildResponse(**r.model_dump()) for r in records]


# ── Opposing Counsel CRUD ────────────────────────────────────────────────────

@router.get("/matters/{matter_id}/opposing-counsel", response_model=list[OpposingCounselResponse])
def list_matter_opposing_counsel(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[OpposingCounselResponse]:
    """List all OC assigned to a matter."""
    m_oc_repo = MatterOpposingCounselRepository(manager)
    oc_repo = OpposingCounselRepository(manager)
    links = m_oc_repo.get_by_matter(matter_id)
    result: list[OpposingCounselResponse] = []
    for link in links:
        oc = oc_repo.select_one(condition={"id": link.opposing_counsel_id})
        if oc:
            result.append(OpposingCounselResponse(**oc.model_dump()))
    return result


@router.patch("/opposing-counsel/{oc_id}", response_model=OpposingCounselResponse)
def update_opposing_counsel(
    oc_id: int,
    body: OpposingCounselUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> OpposingCounselResponse:
    """Update an OC record. Changes propagate to all matters via FK."""
    repo = OpposingCounselRepository(manager)
    if repo.select_one(condition={"id": oc_id}) is None:
        raise HTTPException(status_code=404, detail="Opposing counsel not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    record = repo.update(oc_id, updates)
    return OpposingCounselResponse(**record.model_dump())

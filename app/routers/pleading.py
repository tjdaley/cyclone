"""
app/routers/pleading.py - Pleading ingestion and matter-level claim endpoints.
"""
import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile

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
    MatterChildRequest,
    MatterChildResponse,
    MatterChildUpdateRequest,
    MatterClaimCreateRequest,
    MatterClaimResponse,
    MatterClaimUpdateRequest,
    MatterOpposingCounselLinkRequest,
    MatterOpposingCounselResponse,
    MatterOpposingCounselUpdateRequest,
    MatterPleadingResponse,
    MatterPleadingUpdateRequest,
    OpposingCounselRequest,
    OpposingCounselResponse,
    OpposingCounselUpdateRequest,
    PleadingCommitRequest,
    PleadingCommitResponse,
    PleadingIngestPreviewResponse,
    SignedUrlResponse,
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
        pleading_record, parties_count, children_count, oc_count, claims_count = pleading_service.commit_ingest(
            manager=manager,
            staff_id=staff.id,
            request=body,
            pdf_bytes=None,  # PDF not re-uploaded in commit; preview step stored raw_text only
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    return PleadingCommitResponse(
        pleading=MatterPleadingResponse(**pleading_record.model_dump()),
        opposing_parties_created=parties_count,
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


@router.post("/pleadings/{pleading_id}/pdf", response_model=MatterPleadingResponse)
def upload_pleading_pdf(
    pleading_id: int,
    file: UploadFile = File(...),
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterPleadingResponse:
    """
    Store the original PDF for a pleading and record its storage path.

    Separate from commit because commit takes a JSON body: the frontend still
    holds the file it uploaded for the preview and sends it here once the
    pleading row exists and has an id to name the object by.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    repo = MatterPleadingRepository(manager)
    record = repo.select_one(condition={"id": pleading_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Pleading not found")

    storage = StorageService(manager)
    try:
        storage_path = storage.upload_pleading(record.matter_id, pleading_id, file.file.read())
    except Exception as e:
        LOGGER.error("pleading.upload_pdf: failed for pleading_id=%s: %s", pleading_id, str(e))
        raise HTTPException(status_code=502, detail="Could not store the PDF") from e

    updated = repo.update(pleading_id, {"storage_path": storage_path})
    LOGGER.info("pleading.upload_pdf: stored pleading_id=%s", pleading_id)
    return MatterPleadingResponse(**updated.model_dump())


@router.get("/pleadings/{pleading_id}/pdf-url", response_model=SignedUrlResponse)
def get_pleading_pdf_url(
    pleading_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> SignedUrlResponse:
    """
    Return a short-lived signed URL for the stored PDF.

    JSON rather than a redirect: the browser cannot attach the bearer token to
    a plain link or an iframe, so the SPA fetches the URL here and then opens
    it directly — the signature in the query string is the authorization.
    """
    repo = MatterPleadingRepository(manager)
    record = repo.select_one(condition={"id": pleading_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Pleading not found")
    if not record.storage_path:
        raise HTTPException(status_code=404, detail="No PDF stored for this pleading")

    expires_in = 300
    url = StorageService(manager).get_signed_url(record.storage_path, expires_in=expires_in)
    if not url:
        raise HTTPException(status_code=502, detail="Failed to generate signed URL")
    return SignedUrlResponse(url=url, expires_in=expires_in)


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


@router.post("/matters/{matter_id}/claims", response_model=MatterClaimResponse, status_code=201)
def create_claim(
    matter_id: int,
    body: MatterClaimCreateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterClaimResponse:
    """
    Add a claim/defense to a matter by hand.

    The named pleading must belong to this matter — a claim filed under some
    other matter's pleading would corrupt the matter-level claim view.
    """
    pleading = MatterPleadingRepository(manager).select_one(condition={"id": body.matter_pleading_id})
    if pleading is None:
        raise HTTPException(status_code=404, detail="Pleading not found")
    if pleading.matter_id != matter_id:
        raise HTTPException(status_code=422, detail="That pleading belongs to a different matter")

    repo = MatterClaimRepository(manager)
    record = repo.insert({"matter_id": matter_id, **body.model_dump()})
    LOGGER.info("pleading.create_claim: matter_id=%s claim_id=%s", matter_id, record.id)
    return MatterClaimResponse(**record.model_dump())


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


@router.post("/matters/{matter_id}/children", response_model=MatterChildResponse, status_code=201)
def create_child(
    matter_id: int,
    body: MatterChildRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterChildResponse:
    """
    Add a child to a matter.

    Returns 409 if the matter already has a child with this name and date of
    birth — the same rule pleading ingestion applies.
    """
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")

    duplicate = pleading_service.find_matching_child(manager, matter_id, body.name, body.date_of_birth)
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail="This matter already has a child with that name and date of birth (id=%s)" % duplicate.id,
        )

    repo = MatterChildRepository(manager)
    record = repo.insert({"matter_id": matter_id, **body.model_dump()})
    LOGGER.info("pleading.create_child: matter_id=%s child_id=%s", matter_id, record.id)
    return MatterChildResponse(**record.model_dump())


@router.patch("/children/{child_id}", response_model=MatterChildResponse)
def update_child(
    child_id: int,
    body: MatterChildUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterChildResponse:
    """Update a child. Renaming into an existing child on the matter returns 409."""
    repo = MatterChildRepository(manager)
    existing = repo.select_one(condition={"id": child_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Child not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")

    duplicate = pleading_service.find_matching_child(
        manager,
        existing.matter_id,
        body.name or existing.name,
        body.date_of_birth or existing.date_of_birth,
        ignore_id=child_id,
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail="Another child on this matter already has that name and date of birth (id=%s)" % duplicate.id,
        )

    record = repo.update(child_id, updates)
    LOGGER.info("pleading.update_child: child_id=%s fields=%s", child_id, list(updates))
    return MatterChildResponse(**record.model_dump())


@router.delete("/children/{child_id}", status_code=204)
def delete_child(
    child_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
):
    """Remove a child from a matter."""
    repo = MatterChildRepository(manager)
    if repo.select_one(condition={"id": child_id}) is None:
        raise HTTPException(status_code=404, detail="Child not found")
    repo.delete(child_id)
    LOGGER.info("pleading.delete_child: child_id=%s", child_id)


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


@router.post("/opposing-counsel", response_model=OpposingCounselResponse, status_code=201)
def create_opposing_counsel(
    body: OpposingCounselRequest,
    response: Response,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> OpposingCounselResponse:
    """
    Create an opposing counsel record, or return the existing one.

    Counsel are deduplicated on (bar_state, bar_number) — the same pair the
    database uniquely constrains and pleading ingestion matches on. An
    attorney already known to the firm comes back with 200 and is NOT
    modified here; use PATCH to change their details.
    """
    repo = OpposingCounselRepository(manager)
    existing = repo.get_by_bar_number(body.bar_state, body.bar_number)
    if existing is not None:
        response.status_code = 200
        LOGGER.info("pleading.create_opposing_counsel: returning existing oc_id=%s", existing.id)
        return OpposingCounselResponse(**existing.model_dump())

    record = repo.insert(body.model_dump())
    LOGGER.info("pleading.create_opposing_counsel: created oc_id=%s", record.id)
    return OpposingCounselResponse(**record.model_dump())


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


# ── Matter ↔ Opposing Counsel links ──────────────────────────────────────────

@router.get("/matters/{matter_id}/opposing-counsel/links", response_model=list[MatterOpposingCounselResponse])
def list_matter_counsel_links(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[MatterOpposingCounselResponse]:
    """
    List the association rows for a matter.

    Distinct from GET /matters/{id}/opposing-counsel, which returns the counsel
    records themselves. These carry the link ids, roles, and party assignments
    that the endpoints below act on.
    """
    links = MatterOpposingCounselRepository(manager).get_by_matter(matter_id)
    return [MatterOpposingCounselResponse(**link.model_dump()) for link in links]


@router.post("/matters/{matter_id}/opposing-counsel", response_model=MatterOpposingCounselResponse, status_code=201)
def link_opposing_counsel(
    matter_id: int,
    body: MatterOpposingCounselLinkRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterOpposingCounselResponse:
    """Attach an existing opposing counsel record to a matter."""
    if MatterRepository(manager).select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    if OpposingCounselRepository(manager).select_one(condition={"id": body.opposing_counsel_id}) is None:
        raise HTTPException(status_code=404, detail="Opposing counsel not found")

    repo = MatterOpposingCounselRepository(manager)
    if repo.exists_for_matter(matter_id, body.opposing_counsel_id):
        raise HTTPException(status_code=409, detail="That counsel is already linked to this matter")

    record = repo.insert({"matter_id": matter_id, **body.model_dump()})
    LOGGER.info("pleading.link_opposing_counsel: matter_id=%s oc_id=%s link_id=%s",
                matter_id, body.opposing_counsel_id, record.id)
    return MatterOpposingCounselResponse(**record.model_dump())


@router.patch(
    "/matters/{matter_id}/opposing-counsel/{link_id}",
    response_model=MatterOpposingCounselResponse,
)
def update_matter_counsel_link(
    matter_id: int,
    link_id: int,
    body: MatterOpposingCounselUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> MatterOpposingCounselResponse:
    """Update the role, party, or dates of a matter↔counsel association."""
    repo = MatterOpposingCounselRepository(manager)
    link = repo.select_one(condition={"id": link_id})
    if link is None or link.matter_id != matter_id:
        raise HTTPException(status_code=404, detail="Counsel link not found on this matter")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")

    record = repo.update(link_id, updates)
    LOGGER.info("pleading.update_matter_counsel_link: link_id=%s fields=%s", link_id, list(updates))
    return MatterOpposingCounselResponse(**record.model_dump())


@router.delete("/matters/{matter_id}/opposing-counsel/{link_id}", status_code=204)
def unlink_opposing_counsel(
    matter_id: int,
    link_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
):
    """
    Detach counsel from a matter.

    Only the association is removed. The opposing_counsel record is shared
    across matters and is left alone.
    """
    repo = MatterOpposingCounselRepository(manager)
    link = repo.select_one(condition={"id": link_id})
    if link is None or link.matter_id != matter_id:
        raise HTTPException(status_code=404, detail="Counsel link not found on this matter")
    repo.delete(link_id)
    LOGGER.info("pleading.unlink_opposing_counsel: matter_id=%s link_id=%s", matter_id, link_id)

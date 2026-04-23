"""
app/routers/discovery.py - Discovery document upload, item listing, and response endpoints.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from db.repositories.discovery import (
    DiscoveryDocumentRepository,
    DiscoveryRequestItemRepository,
    DiscoveryResponseRepository,
    StandardObjectionRepository,
    StandardPrivilegeRepository,
)
from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.discovery import (
    DiscoveryDocumentResponse,
    DiscoveryDocumentUpdateRequest,
    DiscoveryRequestItemResponse,
    DiscoveryRequestItemUpdateRequest,
    DiscoveryResponseSchema,
    DiscoveryResponseUpdateRequest,
    DiscoveryUploadResponse,
    StandardObjectionResponse,
    StandardPrivilegeResponse,
)
from services.discovery_service import discovery_service
from services.pdf_service import pdf_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])


# ── Document-level endpoints ─────────────────────────────────────────────────

@router.get("/{matter_id}/documents", response_model=list[DiscoveryDocumentResponse])
def list_documents(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[DiscoveryDocumentResponse]:
    """Return all discovery documents for a matter, newest first."""
    repo = DiscoveryDocumentRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [DiscoveryDocumentResponse(**r.model_dump()) for r in records]


@router.patch("/documents/{document_id}", response_model=DiscoveryDocumentResponse)
def update_document(
    document_id: int,
    body: DiscoveryDocumentUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> DiscoveryDocumentResponse:
    """Update a discovery document (propounded_date, due_date, etc.)."""
    repo = DiscoveryDocumentRepository(manager)
    if repo.select_one(condition={"id": document_id}) is None:
        raise HTTPException(status_code=404, detail="Discovery document not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    record = repo.update(document_id, updates)
    return DiscoveryDocumentResponse(**record.model_dump())


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> Response:
    """Generate and download a Word document with all items and responses."""
    from db.repositories.matter import MatterRepository
    from services.docx_service import generate_discovery_response_docx

    doc_repo = DiscoveryDocumentRepository(manager)
    doc = doc_repo.select_one(condition={"id": document_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Discovery document not found")

    matter_repo = MatterRepository(manager)
    matter = matter_repo.select_one(condition={"id": doc.matter_id})
    matter_name = matter.short_name or matter.matter_name if matter else "Unknown Matter"

    item_repo = DiscoveryRequestItemRepository(manager)
    items = item_repo.get_by_document(document_id)

    docx_bytes = generate_discovery_response_docx(
        document_type=doc.request_type.value,
        matter_name=matter_name,
        items=[i.model_dump() for i in items],
    )

    filename = f"{matter_name} - {doc.request_type.value}.docx".replace(" ", "_")
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Item-level endpoints ─────────────────────────────────────────────────────

@router.get("/documents/{document_id}/items", response_model=list[DiscoveryRequestItemResponse])
def list_items(
    document_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> list[DiscoveryRequestItemResponse]:
    """Return all request items within a discovery document."""
    repo = DiscoveryRequestItemRepository(manager)
    records = repo.get_by_document(document_id)
    return [DiscoveryRequestItemResponse(**r.model_dump()) for r in records]


@router.patch("/items/{item_id}", response_model=DiscoveryRequestItemResponse)
def update_item(
    item_id: int,
    body: DiscoveryRequestItemUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> DiscoveryRequestItemResponse:
    """Update editable fields on a discovery request item."""
    repo = DiscoveryRequestItemRepository(manager)
    if repo.select_one(condition={"id": item_id}) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    LOGGER.info("discovery.update_item: item_id=%s fields=%s", item_id, list(updates))
    record = repo.update(item_id, updates)
    return DiscoveryRequestItemResponse(**record.model_dump())


# ── Standard lookups ─────────────────────────────────────────────────────────

@router.get("/standard-privileges", response_model=list[StandardPrivilegeResponse])
def list_standard_privileges(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[StandardPrivilegeResponse]:
    """Return all standard privilege assertions."""
    repo = StandardPrivilegeRepository(manager)
    return [StandardPrivilegeResponse(**r.model_dump()) for r in repo.get_all()]


@router.get("/standard-objections", response_model=list[StandardObjectionResponse])
def list_standard_objections(
    request_type: str,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[StandardObjectionResponse]:
    """Return standard objections filtered by request type (or '*' wildcard)."""
    valid_types = ("interrogatories", "production", "disclosures", "admissions")
    if request_type not in valid_types:
        raise HTTPException(status_code=422, detail="Invalid request_type. Must be one of: %s" % ", ".join(valid_types))
    repo = StandardObjectionRepository(manager)
    return [StandardObjectionResponse(**r.model_dump()) for r in repo.get_by_request_type(request_type)]


# ── Upload / Ingestion ───────────────────────────────────────────────────────

@router.post("/upload", response_model=DiscoveryUploadResponse, status_code=201)
def upload_discovery(
    request: Request,
    file: UploadFile = File(...),
    matter_id: int = Form(...),
    propounded_date: Optional[str] = Form(default=None),
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> DiscoveryUploadResponse:
    """
    Upload a discovery request PDF for ingestion.

    Extracts text (with LLM vision fallback for image pages), classifies the
    document, extracts individual items, and persists everything.

    The ``propounded_date`` field is optional — if omitted, the service date
    is parsed from the document's certificate of service. The ``staff_id``
    is resolved from the authenticated user's JWT.
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Resolve staff_id from JWT
    staff_repo = StaffRepository(manager)
    staff = staff_repo.get_by_supabase_uid(request.state.supabase_uid)
    if staff is None:
        raise HTTPException(status_code=422, detail="Could not resolve staff member from your login")

    # Extract text from PDF
    pdf_bytes = file.file.read()
    try:
        raw_text = pdf_service.extract_text(pdf_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from the PDF")

    # Parse the override date if provided
    date_override = None
    if propounded_date:
        try:
            date_override = date.fromisoformat(propounded_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="propounded_date must be in YYYY-MM-DD format")

    # Run the ingestion pipeline
    try:
        doc_record, item_records, warnings = discovery_service.ingest(
            manager=manager,
            matter_id=matter_id,
            staff_id=staff.id,
            raw_text=raw_text,
            propounded_date_override=date_override,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Store the original PDF
    try:
        from services.storage_service import StorageService
        storage = StorageService(manager)
        storage_path = storage.upload_discovery(matter_id, doc_record.id, pdf_bytes)
        doc_repo = DiscoveryDocumentRepository(manager)
        doc_record = doc_repo.update(doc_record.id, {"storage_path": storage_path})
    except Exception as e:
        LOGGER.warning("discovery.upload: PDF storage failed (non-fatal): %s", str(e))
        warnings.append("Original PDF could not be stored")

    return DiscoveryUploadResponse(
        document=DiscoveryDocumentResponse(**doc_record.model_dump()),
        item_count=len(item_records),
        items=[DiscoveryRequestItemResponse(**r.model_dump()) for r in item_records],
        warnings=warnings,
    )


# ── Response endpoints (unchanged) ───────────────────────────────────────────

@router.get("/responses/{request_id}", response_model=DiscoveryResponseSchema)
def get_response(
    request_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> DiscoveryResponseSchema:
    """Return the response record for a discovery request item."""
    repo = DiscoveryResponseRepository(manager)
    record = repo.get_by_request(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Response not found")
    return DiscoveryResponseSchema(**record.model_dump())


@router.patch("/responses/{response_id}", response_model=DiscoveryResponseSchema)
def update_response(
    response_id: int,
    body: DiscoveryResponseUpdateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> DiscoveryResponseSchema:
    """Update a discovery response (client draft or attorney review)."""
    repo = DiscoveryResponseRepository(manager)
    if repo.select_one(condition={"id": response_id}) is None:
        raise HTTPException(status_code=404, detail="Response not found")
    updates = body.model_dump(exclude_none=True)
    updates["last_updated_by_uid"] = request.state.supabase_uid
    LOGGER.info("discovery.update_response: response_id=%s", response_id)
    record = repo.update(response_id, updates)
    return DiscoveryResponseSchema(**record.model_dump())

"""
app/routers/clients.py - Client management and conflict-check endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from db.repositories.client import ClientRepository
from dependencies import get_db_manager, require_role
from schemas.client import (
    ClientCreateRequest,
    ClientResponse,
    ClientUpdateRequest,
    ConflictCheckRequest,
    ConflictCheckResponse,
)
from schemas.common import DeletedResponse
from services.conflict_service import ConflictService
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])


@router.get("", response_model=list[ClientResponse])
def list_clients(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[ClientResponse]:
    """Return all client records."""
    repo = ClientRepository(manager)
    records, _ = repo.select_many(condition={}, sort_by="created_at", sort_direction="desc")
    return [ClientResponse(**r.model_dump()) for r in records]


@router.get("/{client_id}", response_model=ClientResponse)
def get_client(
    client_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> ClientResponse:
    """
    Return a single client by ID.

    :raises HTTPException: 404 if not found.
    """
    repo = ClientRepository(manager)
    record = repo.select_one(condition={"id": client_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return ClientResponse(**record.model_dump())


@router.post("", response_model=ClientResponse, status_code=201)
def create_client(
    body: ClientCreateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> ClientResponse:
    """
    Create a new client.

    :raises HTTPException: 409 if the email is already registered.
    """
    repo = ClientRepository(manager)
    if repo.email_exists(body.email):
        raise HTTPException(status_code=409, detail="A client with this email already exists")
    LOGGER.info("clients.create")
    record = repo.insert(body.model_dump())
    return ClientResponse(**record.model_dump())


@router.patch("/{client_id}", response_model=ClientResponse)
def update_client(
    client_id: int,
    body: ClientUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> ClientResponse:
    """Partially update a client record."""
    repo = ClientRepository(manager)
    if repo.select_one(condition={"id": client_id}) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    LOGGER.info("clients.update: client_id=%s", client_id)
    record = repo.update(client_id, updates)
    return ClientResponse(**record.model_dump())


@router.delete("/{client_id}", response_model=DeletedResponse)
def delete_client(
    client_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> DeletedResponse:
    """Delete a client. Admin only."""
    repo = ClientRepository(manager)
    if repo.select_one(condition={"id": client_id}) is None:
        raise HTTPException(status_code=404, detail="Client not found")
    LOGGER.info("clients.delete: client_id=%s", client_id)
    repo.delete(client_id)
    return DeletedResponse(id=client_id)


@router.post("/conflict-check", response_model=ConflictCheckResponse)
def conflict_check(
    body: ConflictCheckRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> ConflictCheckResponse:
    """
    Run a conflict-of-interest check for a prospective client.

    Returns potential hits for attorney/admin review. Conflict details are
    never disclosed to the prospective client.
    """
    LOGGER.info("clients.conflict_check: initiated")
    svc = ConflictService(manager)
    result = svc.check(
        full_name=body.full_name,
        opposing_names=body.opposing_names,
    )
    hits = [
        {"source": h.source, "entity_id": h.entity_id, "matched_field": h.matched_field}
        for h in result.hits
    ]
    return ConflictCheckResponse(
        has_conflict=result.has_conflict,
        hit_count=len(result.hits),
        hits=hits,
    )

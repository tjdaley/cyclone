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
    PortalAccessResponse,
)
from schemas.common import DeletedResponse
from services.conflict_service import ConflictService
from services.portal_access_service import PortalAccessConflict, portal_access_service
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])

_STAFF_ROLES = ["attorney", "admin", "paralegal"]


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
    if "auth_email" in updates:
        try:
            portal_access_service.check_login_email_change(manager, client_id, updates["auth_email"])
        except PortalAccessConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
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
    if portal_access_service.has_access(manager, client_id):
        raise HTTPException(status_code=409, detail="Revoke this client's portal access first")
    LOGGER.info("clients.delete: client_id=%s", client_id)
    repo.delete(client_id)
    return DeletedResponse(id=client_id)


@router.get("/{client_id}/portal-access", response_model=PortalAccessResponse)
def get_portal_access(
    client_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> PortalAccessResponse:
    """Whether this client can sign in to the portal."""
    try:
        return PortalAccessResponse(**portal_access_service.status(manager, client_id))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{client_id}/portal-access", response_model=PortalAccessResponse)
def give_portal_access(
    client_id: int,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> PortalAccessResponse:
    """Let this client sign in with the login email on their record."""
    try:
        return PortalAccessResponse(
            **portal_access_service.give(manager, client_id, request.state.supabase_uid))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except PortalAccessConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/{client_id}/portal-access", response_model=PortalAccessResponse)
def revoke_portal_access(
    client_id: int,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(_STAFF_ROLES)),
) -> PortalAccessResponse:
    """Take portal access away, effective on the client's next request."""
    try:
        return PortalAccessResponse(
            **portal_access_service.revoke(manager, client_id, request.state.supabase_uid))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


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

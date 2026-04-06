"""
app/routers/matters.py - Matter management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException

from db.repositories.matter import (
    BillingSplitRepository,
    MatterRateOverrideRepository,
    MatterRepository,
    MatterStaffRepository,
    OpposingPartyRepository,
)
from dependencies import get_db_manager, require_role
from schemas.common import DeletedResponse
from schemas.matter import (
    MatterCreateRequest,
    MatterRateOverrideRequest,
    MatterRateOverrideResponse,
    MatterResponse,
    MatterStaffRequest,
    MatterUpdateRequest,
    OpposingPartyRequest,
    OpposingPartyResponse,
)
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/matters", tags=["matters"])


@router.get("", response_model=list[MatterResponse])
def list_matters(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[MatterResponse]:
    """Return all matters."""
    repo = MatterRepository(manager)
    records, _ = repo.select_many(condition={}, sort_by="created_at", sort_direction="desc")
    return [MatterResponse(**r.model_dump()) for r in records]


@router.get("/{matter_id}", response_model=MatterResponse)
def get_matter(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal", "client"])),
) -> MatterResponse:
    """Return a single matter by ID."""
    repo = MatterRepository(manager)
    record = repo.select_one(condition={"id": matter_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    return MatterResponse(**record.model_dump())


@router.post("", response_model=MatterResponse, status_code=201)
def create_matter(
    body: MatterCreateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> MatterResponse:
    """Create a new matter."""
    repo = MatterRepository(manager)
    LOGGER.info("matters.create: client_id=%s", body.client_id)
    record = repo.insert(body.model_dump())
    return MatterResponse(**record.model_dump())


@router.patch("/{matter_id}", response_model=MatterResponse)
def update_matter(
    matter_id: int,
    body: MatterUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> MatterResponse:
    """Partially update a matter."""
    repo = MatterRepository(manager)
    if repo.select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    LOGGER.info("matters.update: matter_id=%s", matter_id)
    record = repo.update(matter_id, updates)
    return MatterResponse(**record.model_dump())


@router.delete("/{matter_id}", response_model=DeletedResponse)
def delete_matter(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> DeletedResponse:
    """Delete a matter. Admin only."""
    repo = MatterRepository(manager)
    if repo.select_one(condition={"id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Matter not found")
    LOGGER.info("matters.delete: matter_id=%s", matter_id)
    repo.delete(matter_id)
    return DeletedResponse(id=matter_id)


# ── Rate Overrides ──────────────────────────────────────────────────────────

@router.get("/{matter_id}/rate-overrides", response_model=list[MatterRateOverrideResponse])
def list_rate_overrides(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> list[MatterRateOverrideResponse]:
    """List all per-matter rate overrides for a matter."""
    repo = MatterRateOverrideRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [MatterRateOverrideResponse(**r.model_dump()) for r in records]


@router.post("/{matter_id}/rate-overrides", response_model=MatterRateOverrideResponse, status_code=201)
def set_rate_override(
    matter_id: int,
    body: MatterRateOverrideRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> MatterRateOverrideResponse:
    """Create or replace a per-matter rate override for a staff member."""
    override_repo = MatterRateOverrideRepository(manager)
    existing = override_repo.get_for_staff(matter_id, body.staff_id)
    if existing:
        record = override_repo.update(existing.id, {"rate": body.rate})
    else:
        record = override_repo.insert({"matter_id": matter_id, **body.model_dump()})
    LOGGER.info("matters.set_rate_override: matter_id=%s staff_id=%s", matter_id, body.staff_id)
    return MatterRateOverrideResponse(**record.model_dump())


@router.delete("/{matter_id}/rate-overrides/{override_id}", response_model=DeletedResponse)
def delete_rate_override(
    matter_id: int,
    override_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> DeletedResponse:
    """Remove a rate override."""
    repo = MatterRateOverrideRepository(manager)
    if repo.select_one(condition={"id": override_id, "matter_id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Rate override not found")
    repo.delete(override_id)
    return DeletedResponse(id=override_id)


# ── Matter Staff ────────────────────────────────────────────────────────────

@router.post("/{matter_id}/staff", status_code=201)
def add_matter_staff(
    matter_id: int,
    body: MatterStaffRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin"])),
) -> dict:
    """Assign a staff member to a matter."""
    repo = MatterStaffRepository(manager)
    LOGGER.info("matters.add_staff: matter_id=%s staff_id=%s", matter_id, body.staff_id)
    record = repo.insert({"matter_id": matter_id, **body.model_dump()})
    return record.model_dump()


# ── Opposing Parties ────────────────────────────────────────────────────────

@router.get("/{matter_id}/opposing-parties", response_model=list[OpposingPartyResponse])
def list_opposing_parties(
    matter_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[OpposingPartyResponse]:
    """List opposing parties on a matter."""
    repo = OpposingPartyRepository(manager)
    records = repo.get_by_matter(matter_id)
    return [OpposingPartyResponse(**r.model_dump()) for r in records]


@router.post("/{matter_id}/opposing-parties", response_model=OpposingPartyResponse, status_code=201)
def add_opposing_party(
    matter_id: int,
    body: OpposingPartyRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> OpposingPartyResponse:
    """Add an opposing party to a matter."""
    repo = OpposingPartyRepository(manager)
    LOGGER.info("matters.add_opposing_party: matter_id=%s", matter_id)
    record = repo.insert({"matter_id": matter_id, **body.model_dump()})
    return OpposingPartyResponse(**record.model_dump())


@router.delete("/{matter_id}/opposing-parties/{party_id}", response_model=DeletedResponse)
def delete_opposing_party(
    matter_id: int,
    party_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> DeletedResponse:
    """Remove an opposing party from a matter."""
    repo = OpposingPartyRepository(manager)
    if repo.select_one(condition={"id": party_id, "matter_id": matter_id}) is None:
        raise HTTPException(status_code=404, detail="Opposing party not found")
    repo.delete(party_id)
    return DeletedResponse(id=party_id)

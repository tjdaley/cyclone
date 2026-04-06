"""
app/routers/staff.py - Staff member CRUD endpoints.

All routes require authentication. Role changes (admin only) are also
written to the audit log via the user_roles table.
"""
from fastapi import APIRouter, Depends, HTTPException

from db.repositories.staff import StaffRepository
from dependencies import get_db_manager, require_role
from schemas.common import DeletedResponse
from schemas.staff import StaffCreateRequest, StaffResponse, StaffUpdateRequest
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


@router.get("", response_model=list[StaffResponse])
def list_staff(
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> list[StaffResponse]:
    """
    Return all staff members.

    :return: List of staff records.
    :rtype: list[StaffResponse]
    """
    repo = StaffRepository(manager)
    records, _ = repo.select_many(condition={}, sort_by="created_at")
    return [StaffResponse(**r.model_dump()) for r in records]


@router.get("/{staff_id}", response_model=StaffResponse)
def get_staff(
    staff_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "admin", "paralegal"])),
) -> StaffResponse:
    """
    Return a single staff member by ID.

    :param staff_id: Primary key of the staff record.
    :type staff_id: int
    :return: Staff record.
    :rtype: StaffResponse
    :raises HTTPException: 404 if not found.
    """
    repo = StaffRepository(manager)
    record = repo.select_one(condition={"id": staff_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return StaffResponse(**record.model_dump())


@router.post("", response_model=StaffResponse, status_code=201)
def create_staff(
    body: StaffCreateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> StaffResponse:
    """
    Create a new staff member. Admin only.

    :param body: Staff creation payload.
    :type body: StaffCreateRequest
    :return: Created staff record.
    :rtype: StaffResponse
    :raises HTTPException: 409 if slug is already in use.
    """
    repo = StaffRepository(manager)
    if repo.slug_exists(body.slug):
        raise HTTPException(status_code=409, detail="Slug already in use")
    LOGGER.info("staff.create: slug=%s", body.slug)
    record = repo.insert(body.model_dump())
    return StaffResponse(**record.model_dump())


@router.patch("/{staff_id}", response_model=StaffResponse)
def update_staff(
    staff_id: int,
    body: StaffUpdateRequest,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> StaffResponse:
    """
    Partially update a staff member. Admin only.

    :param staff_id: Primary key of the staff record to update.
    :type staff_id: int
    :param body: Fields to update (all optional).
    :type body: StaffUpdateRequest
    :return: Updated staff record.
    :rtype: StaffResponse
    :raises HTTPException: 404 if not found.
    """
    repo = StaffRepository(manager)
    if repo.select_one(condition={"id": staff_id}) is None:
        raise HTTPException(status_code=404, detail="Staff member not found")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")
    LOGGER.info("staff.update: staff_id=%s", staff_id)
    record = repo.update(staff_id, updates)
    return StaffResponse(**record.model_dump())


@router.delete("/{staff_id}", response_model=DeletedResponse)
def delete_staff(
    staff_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> DeletedResponse:
    """
    Delete a staff member. Admin only.

    :param staff_id: Primary key of the staff record to delete.
    :type staff_id: int
    :return: Deletion confirmation.
    :rtype: DeletedResponse
    :raises HTTPException: 404 if not found.
    """
    repo = StaffRepository(manager)
    if repo.select_one(condition={"id": staff_id}) is None:
        raise HTTPException(status_code=404, detail="Staff member not found")
    LOGGER.info("staff.delete: staff_id=%s", staff_id)
    repo.delete(staff_id)
    return DeletedResponse(id=staff_id)

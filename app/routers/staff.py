"""
app/routers/staff.py - Staff member CRUD endpoints.

All routes require authentication. Role changes (admin only) are also
written to the audit log via the user_roles table.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.user_role import UserRole, UserRoleType
from db.repositories.staff import StaffRepository
from db.repositories.staff_slug_access import StaffSlugAccessRepository
from db.repositories.user_role import UserRoleRepository
from dependencies import get_db_manager, require_role
from schemas.common import DeletedResponse
from schemas.staff import StaffCreateRequest, StaffResponse, StaffUpdateRequest
from util.loggerfactory import LoggerFactory

# Fields a non-admin self-editor is NOT allowed to change on their own record.
# Role is auth-relevant; slug is the lead-attribution identifier and changing
# it would silently re-route every future lead. Admins can change both.
SELF_EDIT_FORBIDDEN_FIELDS = {"role", "slug"}

# Slugs granted to every newly created staff member by default. 'www' is
# the firm's general-inbox slug — lets new hires see and claim unattributed
# leads. Admins can remove this grant if a staff member grabs leads but
# doesn't work them (see lead_service.UNATTRIBUTED_SLUGS).
DEFAULT_STAFF_SLUG_GRANTS = ["www"]

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

    # Seed the user_roles row that auth/RBAC actually reads. Without this,
    # the staff member can authenticate with Google but require_role() will
    # always deny them because user_roles is the source of truth — staff.role
    # is for display/billing only. Skipped when auth_email is null (admin
    # can fill it in later, then re-run this seeding manually).
    if body.auth_email:
        role_repo = UserRoleRepository(manager)
        user_role = UserRole(
            auth_email=body.auth_email,
            role=UserRoleType(body.role.value),
            staff_id=record.id,
        )
        role_repo.insert(user_role.model_dump())
        LOGGER.info("staff.create: seeded user_roles for staff_id=%s role=%s", record.id, body.role.value)

    # Seed default slug-access grants so the new staff member can see the
    # firm's general-inbox leads. Removing these rows later locks them out.
    access_repo = StaffSlugAccessRepository(manager)
    for slug in DEFAULT_STAFF_SLUG_GRANTS:
        access_repo.insert({"staff_id": record.id, "slug": slug})
    LOGGER.info("staff.create: seeded slug_access for staff_id=%s slugs=%s", record.id, DEFAULT_STAFF_SLUG_GRANTS)

    return StaffResponse(**record.model_dump())


@router.patch("/{staff_id}", response_model=StaffResponse)
def update_staff(
    staff_id: int,
    body: StaffUpdateRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["attorney", "paralegal", "admin"])),
) -> StaffResponse:
    """
    Partially update a staff member.

    Authorization: caller must be admin OR be updating their own record. When
    a non-admin updates their own record, the ``role`` and ``slug`` fields are
    silently stripped from the update — those are admin-only changes.

    Side effects (admin only):
    - When ``role`` changes, the matching ``user_roles.role`` is updated so
      RBAC and the staff record stay in sync. If no ``user_roles`` row exists
      yet (legacy staff predating the seeding fix), one is created when
      ``auth_email`` is known.
    """
    repo = StaffRepository(manager)
    target = repo.select_one(condition={"id": staff_id})
    if target is None:
        raise HTTPException(status_code=404, detail="Staff member not found")

    caller_role = getattr(request.state, "role", None)
    caller_uid = getattr(request.state, "supabase_uid", None)
    is_admin = caller_role == "admin"
    is_self = caller_uid is not None and target.supabase_uid == caller_uid

    if not (is_admin or is_self):
        raise HTTPException(status_code=403, detail="You can only edit your own staff record")

    updates = body.model_dump(exclude_none=True)
    if not is_admin:
        for field in SELF_EDIT_FORBIDDEN_FIELDS:
            updates.pop(field, None)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided for update")

    LOGGER.info("staff.update: staff_id=%s by=%s self=%s admin=%s", staff_id, caller_uid, is_self, is_admin)
    record = repo.update(staff_id, updates)

    # Keep user_roles.role aligned with staff.role when an admin changes it.
    if is_admin and "role" in updates and updates["role"] != target.role.value:
        role_repo = UserRoleRepository(manager)
        existing = role_repo.get_by_staff(staff_id)
        new_role = UserRoleType(updates["role"])
        if existing is not None:
            role_repo.update(existing.id, {"role": new_role.value})
            LOGGER.info("staff.update: synced user_roles.role staff_id=%s -> %s", staff_id, new_role.value)
        elif record.auth_email:
            # Legacy backfill: create the user_roles row if it's missing
            role_repo.insert(UserRole(
                auth_email=record.auth_email,
                role=new_role,
                staff_id=staff_id,
            ).model_dump())
            LOGGER.info("staff.update: backfilled user_roles staff_id=%s role=%s", staff_id, new_role.value)

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

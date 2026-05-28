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
from schemas.staff import (
    StaffCreateRequest, StaffResponse, StaffRolesRequest, StaffRolesResponse, StaffUpdateRequest,
)
from util.loggerfactory import LoggerFactory

# Roles assignable to a STAFF member through the admin UI. 'client' exists as a
# UserRoleType but is for client-portal users (client_id populated, not staff_id).
_STAFF_ASSIGNABLE_ROLES: set[str] = {
    UserRoleType.attorney.value,
    UserRoleType.paralegal.value,
    UserRoleType.admin.value,
}

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


def _roles_for_staff_ids(manager, staff_ids: list[int]) -> dict[int, list[str]]:
    """Return a {staff_id: [role values]} map for the given ids. One query (IN clause)."""
    if not staff_ids:
        return {}
    rows, _ = UserRoleRepository(manager).select_many(condition={"staff_id": staff_ids})
    out: dict[int, list[str]] = {}
    for row in rows:
        if row.staff_id is not None:
            out.setdefault(row.staff_id, []).append(row.role.value)
    return {sid: sorted(set(values)) for sid, values in out.items()}


def _single_staff_roles(manager, staff_id: int) -> list[str]:
    """Return sorted role values for one staff member."""
    rows = UserRoleRepository(manager).get_by_staff(staff_id)
    return sorted({r.role.value for r in rows})


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
    roles_map = _roles_for_staff_ids(manager, [r.id for r in records])
    return [
        StaffResponse(**r.model_dump(), roles=roles_map.get(r.id, []))
        for r in records
    ]


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
    return StaffResponse(**record.model_dump(), roles=_single_staff_roles(manager, staff_id))


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

    return StaffResponse(**record.model_dump(), roles=_single_staff_roles(manager, record.id))


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

    # Best-effort sync: if the staff member has a user_roles row matching the
    # OLD staff.role value, retitle it to the NEW value so RBAC stays aligned.
    # Any other roles they hold (e.g. a separate 'admin' grant) are preserved
    # untouched. No new rows are created — under the multi-role model,
    # granting/revoking auth roles is managed independently of staff.role.
    if is_admin and "role" in updates and updates["role"] != target.role.value:
        role_repo = UserRoleRepository(manager)
        old_role_value = target.role.value
        new_role_value = updates["role"]
        synced = 0
        for row in role_repo.get_by_staff(staff_id):
            if row.role.value == old_role_value:
                role_repo.update(row.id, {"role": new_role_value})
                synced += 1
        if synced:
            LOGGER.info(
                "staff.update: synced %s user_roles row(s) staff_id=%s %s -> %s",
                synced, staff_id, old_role_value, new_role_value,
            )
        else:
            LOGGER.info(
                "staff.update: no matching user_roles row for old role; auth roles unchanged staff_id=%s",
                staff_id,
            )

    return StaffResponse(**record.model_dump(), roles=_single_staff_roles(manager, staff_id))


@router.get("/{staff_id}/roles", response_model=StaffRolesResponse)
def get_staff_roles(
    staff_id: int,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> StaffRolesResponse:
    """Return the auth roles assigned to a staff member."""
    rows = UserRoleRepository(manager).get_by_staff(staff_id)
    return StaffRolesResponse(
        staff_id=staff_id,
        roles=sorted({r.role.value for r in rows}),
    )


@router.put("/{staff_id}/roles", response_model=StaffRolesResponse)
def set_staff_roles(
    staff_id: int,
    body: StaffRolesRequest,
    request: Request,
    manager=Depends(get_db_manager),
    _=Depends(require_role(["admin"])),
) -> StaffRolesResponse:
    """
    Replace the set of auth roles for a staff member. Diffs against the
    current set; inserts/deletes only what changed. Self-lockout protected:
    an admin cannot remove their own 'admin' role.
    """
    staff_repo = StaffRepository(manager)
    target = staff_repo.select_one(condition={"id": staff_id})
    if target is None:
        raise HTTPException(status_code=404, detail="Staff member not found")

    requested = {r.strip() for r in body.roles if r.strip()}
    invalid = requested - _STAFF_ASSIGNABLE_ROLES
    if invalid:
        raise HTTPException(
            status_code=400,
            detail="Invalid roles for staff: %s (allowed: %s)" % (sorted(invalid), sorted(_STAFF_ASSIGNABLE_ROLES)),
        )

    role_repo = UserRoleRepository(manager)
    current_rows = role_repo.get_by_staff(staff_id)
    current_by_role: dict[str, int] = {r.role.value: r.id for r in current_rows}
    current_roles: set[str] = set(current_by_role.keys())

    # Self-lockout guard: the caller cannot remove their own admin role.
    caller_uid = getattr(request.state, "supabase_uid", None)
    if (
        caller_uid is not None
        and target.supabase_uid == caller_uid
        and UserRoleType.admin.value in current_roles
        and UserRoleType.admin.value not in requested
    ):
        raise HTTPException(
            status_code=400,
            detail="You cannot remove your own admin role. Have another admin do it.",
        )

    to_add = requested - current_roles
    to_remove_ids = [row_id for role, row_id in current_by_role.items() if role not in requested]

    # New rows inherit supabase_uid + auth_email from the target so they're
    # linked immediately if the staff member has already correlated; otherwise
    # they sit unlinked and get picked up by correlate-staff on first login.
    for role in to_add:
        role_repo.insert(UserRole(
            supabase_uid=target.supabase_uid,
            auth_email=target.auth_email,
            role=UserRoleType(role),
            staff_id=staff_id,
        ).model_dump())
    for row_id in to_remove_ids:
        role_repo.delete(row_id)

    LOGGER.info(
        "staff.set_roles: staff_id=%s added=%s removed=%s",
        staff_id, sorted(to_add), sorted(current_roles - requested),
    )
    return StaffRolesResponse(staff_id=staff_id, roles=sorted(requested))


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

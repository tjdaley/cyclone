"""
app/routers/auth_flow.py - Authentication correlation and profile endpoints.

These routes sit behind AuthMiddleware (valid JWT required) but do NOT use
require_role() — they are called at the point when a user has authenticated
with Supabase but may not yet have a role record in user_roles.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.user_role import UserRole, UserRoleType
from db.repositories.staff import StaffRepository
from db.repositories.user_role import UserRoleRepository
from db.supabasemanager import SupabaseManager
from dependencies import get_db_manager
from services.audit_logger import AuditLogger
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me")
def get_me(
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
) -> dict:
    """
    Return the current user's application profile.

    Called by the frontend immediately after login to determine whether the
    user has a role assigned and where to redirect them.

    - **200** — user has a role; returns ``{role, staff_id, client_id}``.
    - **404** — user has no role yet; frontend should trigger ``/correlate-staff``.

    :return: Role record dict.
    :rtype: dict
    :raises HTTPException: 404 if no role is assigned to this user.
    """
    uid: str = request.state.supabase_uid
    role_repo = UserRoleRepository(manager)
    record = role_repo.get_by_uid(uid)
    if record is None:
        raise HTTPException(status_code=404, detail="No role assigned")
    return record.model_dump()


@router.post("/correlate-staff")
def correlate_staff(
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
) -> dict:
    """
    First-login staff correlation flow.

    Matches the Supabase Auth session email against ``staff.auth_email``
    for records that have not yet been linked (``supabase_uid IS NULL``).
    On a successful match:

    1. Writes the Auth UID into ``staff.supabase_uid``.
    2. Creates a ``user_roles`` record linking the UID to the staff role.
    3. Returns the user profile (same shape as ``GET /me``).

    This endpoint is idempotent: if the link already exists it returns the
    existing role record rather than raising an error.

    :return: Role record dict.
    :rtype: dict
    :raises HTTPException: 404 if no unlinked staff record matches the login email.
    :raises HTTPException: 422 if the JWT does not carry an email claim.
    """
    uid: str = request.state.supabase_uid
    email: str | None = getattr(request.state, "email", None)

    if not email:
        raise HTTPException(
            status_code=422,
            detail="No email claim in JWT — ensure the Supabase project is configured to include email",
        )

    role_repo = UserRoleRepository(manager)
    staff_repo = StaffRepository(manager)

    # Idempotency: if already linked, return the existing record
    existing = role_repo.get_by_uid(uid)
    if existing is not None:
        LOGGER.info("auth.correlate_staff: already linked uid=%s", uid)
        return existing.model_dump()

    # Find an unlinked staff record whose auth_email matches the login email
    staff_record = staff_repo.select_one(
        condition={"auth_email": email, "supabase_uid": None}
    )
    if staff_record is None:
        LOGGER.warning(
            "auth.correlate_staff: no unlinked staff found for email (redacted)"
        )
        raise HTTPException(
            status_code=404,
            detail="No staff account is awaiting activation for this email address. "
                   "Contact your administrator.",
        )

    # Link the Auth UID to the staff record
    staff_repo.update(staff_record.id, {"supabase_uid": uid})
    LOGGER.info("auth.correlate_staff: linked staff_id=%s", staff_record.id)

    # Create the user_roles record
    role_obj = UserRole(
        supabase_uid=uid,
        role=UserRoleType(staff_record.role.value),
        staff_id=staff_record.id,
        client_id=None,
    )
    role_record = role_repo.insert(role_obj.model_dump())

    audit = AuditLogger(manager)
    audit.log(
        supabase_uid=uid,
        action="user_role.assigned",
        entity_type="user_role",
        entity_id=str(role_record.id),
        after_json=role_record.model_dump(),
    )

    return role_record.model_dump()

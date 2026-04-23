"""
app/routers/auth_flow.py - Authentication correlation and profile endpoints.

These routes sit behind AuthMiddleware (valid JWT required) but do NOT use
require_role() — they are called at the point when a user has authenticated
with Supabase but may not yet have a role record in user_roles.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from db.repositories.staff import StaffRepository
from db.repositories.user_role import UserRoleRepository
from db_handler import SupabaseManager
from dependencies import get_db_manager
from services.audit_logger import AuditLogger
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me")
def get_me(
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Return the current user's application profile.

    Called by the frontend immediately after login to determine whether the
    user has a role assigned and where to redirect them.

    Single-query lookup: ``user_roles WHERE supabase_uid = <jwt sub>``.

    - **200** — user has a role; returns ``{role, staff_id, client_id, ...}``.
    - **404** — user has no role yet; frontend should redirect to ``/onboarding``.

    :return: Role record dict.
    :rtype: dict[str, Any]
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
) -> dict[str, Any]:
    """
    First-login staff correlation flow.

    Matches the JWT email against ``user_roles.auth_email`` for records that
    have not yet been linked (``supabase_uid IS NULL``). On a successful match:

    1. Writes the Auth UID into ``user_roles.supabase_uid``.
    2. Writes the Auth UID into the corresponding ``staff.supabase_uid``.
    3. Returns the user profile (same shape as ``GET /me``).

    This endpoint is idempotent: if the link already exists it returns the
    existing role record rather than raising an error.

    :return: Role record dict.
    :rtype: dict[str, Any]
    :raises HTTPException: 404 if no unlinked role record matches the login email.
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

    # Find an unlinked user_roles record whose auth_email matches the login email
    role_record = role_repo.get_by_auth_email(email)
    if role_record is None:
        LOGGER.warning(
            "auth.correlate_staff: no unlinked role found for email (redacted)"
        )
        raise HTTPException(
            status_code=404,
            detail="No account is awaiting activation for this email address. "
                   "Contact your administrator.",
        )

    # Link the Auth UID to the user_roles record
    role_repo.update(role_record.id, {"supabase_uid": uid})
    LOGGER.info("auth.correlate_staff: linked role_id=%s uid=%s", role_record.id, uid)

    # Also link the Auth UID to the corresponding staff record
    if role_record.staff_id is not None:
        staff_repo.update(role_record.staff_id, {"supabase_uid": uid})
        LOGGER.info("auth.correlate_staff: linked staff_id=%s", role_record.staff_id)

    audit = AuditLogger(manager)
    audit.log(
        supabase_uid=uid,
        action="user_role.correlated",
        entity_type="user_role",
        entity_id=str(role_record.id),
        after_json=role_record.model_dump(),
    )

    # Re-fetch to get the updated record with supabase_uid populated
    updated = role_repo.get_by_uid(uid)
    return (updated or role_record).model_dump()

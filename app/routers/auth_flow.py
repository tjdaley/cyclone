"""
app/routers/auth_flow.py - Authentication correlation and profile endpoints.

These routes sit behind AuthMiddleware (valid JWT required) but do NOT use
require_role() — they are called at the point when a user has authenticated
with Supabase but may not yet have a role record in user_roles.

A user may hold multiple roles. The /me response carries the full list (under
``roles``) plus a derived ``role`` field that's the highest-privilege role
(via primary_role) for legacy single-role consumers.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from db.models.user_role import UserRoleInDB, primary_role
from db.repositories.staff import StaffRepository
from db.repositories.user_role import UserRoleRepository
from db_handler import SupabaseManager
from dependencies import get_db_manager
from services.audit_logger import AuditLogger
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _build_profile(supabase_uid: str, records: list[UserRoleInDB]) -> dict[str, Any]:
    """
    Aggregate a user's role rows into the response shape consumed by /me.

    - ``roles``: sorted list of every role value the user holds.
    - ``role``:  the highest-privilege one, for legacy single-role consumers
      (kept so the existing frontend doesn't need to change everywhere).
    - ``staff_id`` / ``client_id``: pulled from any row that has it; all rows
      for a given user should agree.
    """
    role_values = [r.role.value for r in records]
    return {
        "supabase_uid": supabase_uid,
        "roles": sorted(set(role_values)),
        "role": primary_role(role_values),
        "staff_id": next((r.staff_id for r in records if r.staff_id is not None), None),
        "client_id": next((r.client_id for r in records if r.client_id is not None), None),
        "auth_email": next((r.auth_email for r in records if r.auth_email is not None), None),
    }


@router.get("/me")
def get_me(
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    Return the current user's application profile.

    Called by the frontend immediately after login to determine whether the
    user has a role assigned and where to redirect them.

    - **200** — user has at least one role; returns the aggregated profile.
    - **404** — user has no role yet; frontend should redirect to ``/onboarding``.
    """
    uid: str = request.state.supabase_uid
    records = UserRoleRepository(manager).get_by_uid(uid)
    if not records:
        raise HTTPException(status_code=404, detail="No role assigned")
    return _build_profile(uid, records)


@router.post("/correlate-staff")
def correlate_staff(
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
) -> dict[str, Any]:
    """
    First-login staff correlation flow.

    Finds EVERY unlinked ``user_roles`` row whose ``auth_email`` matches the
    JWT email and writes the Auth UID into each. The corresponding ``staff``
    record is updated once. Idempotent: if any rows are already linked for
    this UID, returns the aggregated profile without changes.

    :raises HTTPException: 404 if no unlinked row matches the login email.
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

    # Idempotency: if any rows are already linked for this UID, we're done.
    existing = role_repo.get_by_uid(uid)
    if existing:
        LOGGER.info("auth.correlate_staff: already linked uid=%s rows=%s", uid, len(existing))
        return _build_profile(uid, existing)

    unlinked = role_repo.get_unlinked_by_auth_email(email)
    if not unlinked:
        LOGGER.warning("auth.correlate_staff: no unlinked rows for email (redacted)")
        raise HTTPException(
            status_code=404,
            detail="No account is awaiting activation for this email address. "
                   "Contact your administrator.",
        )

    # Link every matching unlinked row. Link the corresponding staff record once.
    audit = AuditLogger(manager)
    linked_staff_ids: set[int] = set()
    for row in unlinked:
        role_repo.update(row.id, {"supabase_uid": uid})
        LOGGER.info("auth.correlate_staff: linked role_id=%s role=%s", row.id, row.role.value)
        if row.staff_id is not None and row.staff_id not in linked_staff_ids:
            staff_repo.update(row.staff_id, {"supabase_uid": uid})
            linked_staff_ids.add(row.staff_id)
            LOGGER.info("auth.correlate_staff: linked staff_id=%s", row.staff_id)
        audit.log(
            supabase_uid=uid,
            action="user_role.correlated",
            entity_type="user_role",
            entity_id=str(row.id),
            after_json=row.model_dump(),
        )

    after = role_repo.get_by_uid(uid)
    return _build_profile(uid, after or unlinked)

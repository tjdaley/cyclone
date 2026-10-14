"""
app/dependencies.py - Shared FastAPI dependency functions.

All route handlers that need a database manager or role enforcement use
these via Depends(). Do not instantiate SupabaseManager anywhere else.
"""
from typing import Callable, Union

from fastapi import Depends, HTTPException, Request

from db_handler import SupabaseManager
from db.models.user_role import primary_role
from db.repositories.user_role import UserRoleRepository
from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)


def get_db_manager() -> SupabaseManager:
    """
    FastAPI dependency that yields a fresh SupabaseManager instance per request.

    SupabaseManager is not thread-safe across requests, so we instantiate
    one per request rather than sharing a module-level singleton.

    :return: Configured SupabaseManager instance.
    :rtype: SupabaseManager
    """
    return SupabaseManager()


def get_landing_pages_db() -> SupabaseManager:
    """
    FastAPI dependency that yields a SupabaseManager pointed at the
    landing-pages project (read-only source of leads + attorneys).

    Construction is explicit so we don't have to monkey with environment
    variables to switch projects — the same code path is used for cyclone's
    own DB via get_db_manager(), just with different credentials.

    verify_connection=False skips the get_user() probe because the
    service-role key does not represent a user session.

    :return: SupabaseManager bound to the landing-pages project.
    :rtype: SupabaseManager
    """
    return SupabaseManager(
        url=settings.supabase_landing_pages_url,
        key=settings.supabase_landing_pages_service_role_key,
        verify_connection=False,
    )


def get_current_user(request: Request) -> dict[str, Union[str, None]]:
    """
    FastAPI dependency that returns the authenticated user's identity.

    Requires AuthMiddleware to have already injected ``supabase_uid`` and
    ``role`` into ``request.state``. Returns a dict with those values for
    use in route handlers that need the caller's identity.

    :param request: Current Starlette request (injected by FastAPI).
    :type request: Request
    :return: Dict with ``supabase_uid`` and ``role`` keys.
    :rtype: dict
    :raises HTTPException: 401 if the middleware did not populate state
        (should not happen in normal operation — middleware blocks first).
    """
    uid = getattr(request.state, "supabase_uid", None)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "supabase_uid": uid,
        "role": getattr(request.state, "role", None),
    }


def require_role(allowed_roles: list[str]) -> Callable[..., None]:
    """
    FastAPI dependency factory that enforces role-based access control.

    Resolves the caller's roles from the ``user_roles`` table (authoritative
    source). A user may hold MULTIPLE roles; access is granted if any of them
    is in ``allowed_roles``. After a successful check:

    - ``request.state.roles`` is set to the sorted list of all the user's roles.
    - ``request.state.role``  is set to the primary (highest-privilege) role,
      preserved for code that still wants a single string.

    Usage::

        @router.get("/secret")
        def secret_route(_=Depends(require_role(["admin"]))):
            ...
    """

    def _check(request: Request, manager: SupabaseManager = Depends(get_db_manager)) -> None:
        uid = getattr(request.state, "supabase_uid", None)
        if uid is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        records = UserRoleRepository(manager).get_by_uid(uid)
        if not records:
            LOGGER.warning("require_role: no role record found for uid=%s", uid)
            raise HTTPException(status_code=403, detail="No role assigned to this account")

        user_roles = sorted({r.role.value for r in records})
        if not (set(user_roles) & set(allowed_roles)):
            LOGGER.warning(
                "require_role: access denied roles=%s allowed=%s",
                user_roles,
                allowed_roles,
            )
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        # Inject DB-verified roles into request.state. role = primary (for
        # legacy single-role callers); roles = the full set.
        request.state.role = primary_role(user_roles)
        request.state.roles = user_roles

    return _check


def require_client(
    request: Request,
    manager: SupabaseManager = Depends(get_db_manager),
) -> int:
    """
    The client id behind this request, for the portal.

    **This is the only way a portal endpoint learns who it is serving.** The id
    comes from the caller's ``user_roles`` rows, never from the request — a
    client_id in a path or a body would be a request to read somebody else's
    matters, and honouring it would be the whole failure.

    Staff are refused here even though they may see more elsewhere. The portal
    is a different surface with different wording and a different audience, and
    a staff member who wants a matter's records has the staff app; letting them
    in through this door would mean every portal response had to be correct for
    two audiences at once.

    :return: The client id.
    :rtype: int
    :raises HTTPException: 401 unauthenticated, 403 when the account is not a
        client or carries no client record.
    """
    uid = getattr(request.state, "supabase_uid", None)
    if uid is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    records = UserRoleRepository(manager).get_by_uid(uid)
    if not records:
        raise HTTPException(status_code=403, detail="No role assigned to this account")

    roles = sorted({r.role.value for r in records})
    client_id = next((r.client_id for r in records if r.client_id is not None), None)
    if "client" not in roles or client_id is None:
        LOGGER.warning("require_client: refused uid=%s roles=%s client_id=%s",
                       uid, roles, client_id)
        raise HTTPException(status_code=403, detail="This area is for client accounts")

    request.state.role = primary_role(roles)
    request.state.roles = roles
    request.state.client_id = client_id
    return client_id

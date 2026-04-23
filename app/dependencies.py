"""
app/dependencies.py - Shared FastAPI dependency functions.

All route handlers that need a database manager or role enforcement use
these via Depends(). Do not instantiate SupabaseManager anywhere else.
"""
from typing import Callable, Union

from fastapi import Depends, HTTPException, Request

from db_handler import SupabaseManager
from db.repositories.user_role import UserRoleRepository
from util.loggerfactory import LoggerFactory

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

    Resolves the caller's role from the ``user_roles`` table (authoritative
    source) rather than relying solely on the JWT claim. This prevents
    privilege escalation if a JWT claim is stale relative to the database.

    Usage::

        @router.get("/secret")
        def secret_route(_=Depends(require_role(["admin"]))):
            ...

    :param allowed_roles: List of role strings that may access the route.
    :type allowed_roles: list[str]
    :return: FastAPI dependency callable.
    :rtype: Callable
    """

    def _check(request: Request, manager: SupabaseManager = Depends(get_db_manager)) -> None:
        uid = getattr(request.state, "supabase_uid", None)
        if uid is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Single-query lookup: user_roles WHERE supabase_uid = uid
        role_repo = UserRoleRepository(manager)
        role_record = role_repo.get_by_uid(uid)
        if role_record is None:
            LOGGER.warning("require_role: no role record found for uid=%s", uid)
            raise HTTPException(status_code=403, detail="No role assigned to this account")

        if role_record.role.value not in allowed_roles:
            LOGGER.warning(
                "require_role: access denied role=%s allowed=%s",
                role_record.role.value,
                allowed_roles,
            )
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        # Inject the DB-verified role back into request.state
        request.state.role = role_record.role.value

    return _check

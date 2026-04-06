"""
app/middleware/auth_middleware.py - Supabase JWT validation middleware.

Validates the Bearer token on every request (except excluded paths) and
injects ``supabase_uid`` and ``role`` into ``request.state`` for use by
downstream ``require_role()`` dependencies.
"""
from typing import Optional

from fastapi import Request, Response
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from util.loggerfactory import LoggerFactory
from util.settings import settings

LOGGER = LoggerFactory.create_logger(__name__)

# Paths that do not require authentication
_EXCLUDED_PATHS: set[str] = {
    "/api/health",
    "/api/config",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that validates Supabase JWTs on every request.

    On success: sets ``request.state.supabase_uid`` and
    ``request.state.role`` (from the ``user_metadata.role`` claim or the
    ``user_roles`` table lookup performed in ``dependencies.py``).

    On failure: returns 401 JSON immediately; the request never reaches a
    route handler.

    The ``role`` in ``request.state`` is the raw claim from the JWT. Route
    handlers use ``require_role()`` (see ``dependencies.py``) for
    authoritative role enforcement against the ``user_roles`` table.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Pass through excluded paths without token validation
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)

        # OPTIONS preflight passes through for CORS
        if request.method == "OPTIONS":
            return await call_next(request)

        token = _extract_bearer_token(request)
        if token is None:
            LOGGER.warning("AuthMiddleware: missing Authorization header: path=%s", request.url.path)
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        uid, role, email = _decode_token(token)
        if uid is None:
            LOGGER.warning("AuthMiddleware: invalid or expired token: path=%s", request.url.path)
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        request.state.supabase_uid = uid
        request.state.role = role    # May be None; authoritative check is in require_role()
        request.state.email = email  # Used by the auth correlation endpoint
        LOGGER.debug("AuthMiddleware: authenticated uid=%s role=%s", uid, role)

        return await call_next(request)


def _extract_bearer_token(request: Request) -> Optional[str]:
    """
    Extract the Bearer token from the Authorization header.

    :param request: Incoming Starlette request.
    :type request: Request
    :return: Raw JWT string, or ``None`` if the header is absent or malformed.
    :rtype: Optional[str]
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None


def _decode_token(token: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Decode and validate a Supabase JWT using the configured JWT secret.

    :param token: Raw JWT string from the Authorization header.
    :type token: str
    :return: Tuple of (supabase_uid, role_claim, email); all ``None`` on failure.
    :rtype: tuple[Optional[str], Optional[str], Optional[str]]
    """
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},  # Supabase JWTs don't always include aud
        )
        uid: Optional[str] = payload.get("sub")
        email: Optional[str] = payload.get("email")
        # Role may be in user_metadata or app_metadata depending on Supabase config
        user_metadata: dict = payload.get("user_metadata", {})
        app_metadata: dict = payload.get("app_metadata", {})
        role: Optional[str] = user_metadata.get("role") or app_metadata.get("role")
        return uid, role, email
    except JWTError as e:
        LOGGER.warning("AuthMiddleware: JWT decode error: %s", str(e))
        return None, None, None

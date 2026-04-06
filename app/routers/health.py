"""
app/routers/health.py - Utility routes: health check and public config.

These routes are excluded from authentication by AuthMiddleware.
"""
from fastapi import APIRouter

from schemas.common import MessageResponse
from util.settings import settings

router = APIRouter(tags=["utility"])


@router.get("/api/health", response_model=MessageResponse)
def health_check() -> MessageResponse:
    """
    Liveness probe — returns 200 if the API process is running.

    :return: Static OK message.
    :rtype: MessageResponse
    """
    return MessageResponse(message="ok")


@router.get("/api/config")
def public_config() -> dict:
    """
    Return public configuration values safe to expose to the frontend.

    The Stripe publishable key is fetched here rather than baked into the
    frontend build so it can be rotated without a redeploy.

    :return: Dict of safe-to-expose config values.
    :rtype: dict
    """
    return {
        "stripe_publishable_key": settings.stripe_publishable_key,
        "firm_name": settings.firm_name,
        "time_increment_options": settings.time_increment_options,
        "version": settings.version,
    }

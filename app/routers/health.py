"""
app/routers/health.py - Utility routes: health check and public config.

These routes are excluded from authentication by AuthMiddleware.
"""
from typing import Any

from fastapi import APIRouter, Depends

from dependencies import require_role
from schemas.common import MessageResponse
from util.config_fingerprint import detailed_fingerprint, public_fingerprint
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
def public_config() -> dict[str, Any]:
    """
    Return public configuration values safe to expose to the frontend.

    The Stripe publishable key is fetched here rather than baked into the
    frontend build so it can be rotated without a redeploy. ``id`` identifies
    which server instance answered, so the UI can display it.

    :return: Dict of safe-to-expose config values.
    :rtype: dict
    """
    return {
        "stripe_publishable_key": settings.stripe_publishable_key,
        "firm_name": settings.firm_name,
        "time_increment_options": settings.time_increment_options,
        "referral_types": settings.referral_types,  # Client intake dropdown values
        "version": settings.version,
        "id": settings.id,
        # A hash over the configuration that is supposed to be identical on
        # every node. Two nodes disagreeing is drift, and this is what turns
        # finding it from luck into a one-line diff:
        #
        #   curl app-a/api/config | jq -r .config_fingerprint.fleet_hash
        #   curl app-b/api/config | jq -r .config_fingerprint.fleet_hash
        #
        # Unauthenticated on purpose — a hash reveals nothing, and needing a
        # credential is what would stop anybody actually running the check.
        # The breakdown naming WHICH setting differs is behind auth below.
        "config_fingerprint": public_fingerprint(settings),
    }


@router.get("/api/v1/config/fingerprint")
def config_fingerprint_detail(
    _=Depends(require_role(["admin", "attorney", "paralegal", "staff"])),
) -> dict[str, Any]:
    """
    The per-setting breakdown behind the public fleet hash.

    The hash says two nodes disagree; this says which setting. Values are never
    returned — each is a digest, so a comparison proves agreement without
    printing a credential, and the same output is safe to paste into a ticket.

    Behind auth because the set of digests is an inventory: which credentials
    this node holds and which it does not. That is not a secret, but it is a
    map, and a map is worth a login.

    ``infrastructure`` is returned in the clear and labelled, because a reader
    comparing two nodes has to see that ``id`` differing is expected rather
    than wonder whether it was overlooked.
    """
    return detailed_fingerprint(settings)

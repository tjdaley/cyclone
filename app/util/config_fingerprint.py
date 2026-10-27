"""
app/util/config_fingerprint.py - Proving two servers agree.

Drift between two nodes' ``.env`` files was found by accident, and "whether it
was consequential is a matter of luck, not design". This is the design: every
node publishes a short hash of the configuration that is supposed to be
identical across the fleet, so disagreement is a diff rather than a discovery.

    curl app-a/api/config | jq -r .config.fleet_hash   ->  7f3a91c2
    curl app-b/api/config | jq -r .config.fleet_hash   ->  b40e55d1   <- drift

**A secret is hashed, never read.** Each credential contributes
``sha256(value)[:12]`` — enough to prove two nodes hold the same key, useless
for recovering it. An unset secret contributes the literal ``<unset>``, which
is a different fact from a set one and has to stay distinguishable: "both nodes
have no Stripe key" and "both nodes have the same Stripe key" are not the same
state, and a scheme that collapsed them would hide the more dangerous half.

**Only what should match is hashed.** ``id``, ``host_url`` and the Supabase and
Redis URLs are excluded — they differ per node by design, and including them
would make every pair of nodes disagree forever. See ``config_spec``.

**The hash is safe to expose unauthenticated**, which is the point: it rides on
``GET /api/config`` beside ``id`` and ``version``, so the check is a curl from a
laptop with no credential and no SSH. The per-key breakdown that says *which*
setting differs is not public — digests of unset-vs-set state across a dozen
credentials is a map of what a node has, and that belongs behind auth.
"""
import hashlib
import json
from datetime import date
from typing import Any

from util.config_spec import (
    DEPRECATED,
    DEPRECATION_REASON,
    FINGERPRINTED,
    INFRASTRUCTURE,
    SECRET,
    bucket_of,
)
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

#: Twelve hex characters. Long enough that two different values colliding is
#: not a practical concern for a fleet of a handful of nodes; short enough to
#: read aloud on a call, which is how this actually gets used.
_DIGEST_CHARS = 12

#: A setting with no value. Deliberately distinct from a digest, so "unset on
#: both" and "the same value on both" never look alike.
UNSET = "<unset>"


def _canonical(value: Any) -> str:
    """
    One stable string for a value, whatever its type.

    ``json.dumps`` with sorted keys rather than ``str()``: a list of floats
    renders identically across Python versions and a dict cannot reorder itself
    between two nodes and read as drift.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return json.dumps(value)
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def digest(value: Any) -> str:
    """
    The published form of one setting's value.

    Secrets never reach this as themselves — see :func:`field_digests`, which
    routes them here only after deciding they are secret. The function is the
    same for both kinds so that a secret and a non-secret of the same value
    cannot be told apart by the SHAPE of what is published.
    """
    text = _canonical(value)
    if value is None or value == "" or value == []:
        return UNSET
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def field_digests(settings: Any) -> dict[str, str]:
    """
    Every fingerprinted setting and its digest, sorted by name.

    Non-secret values are digested too rather than published in the clear. That
    is not secrecy theatre — it keeps the comparison uniform, so the *same*
    operation answers "do these two nodes agree" for a model temperature and
    for an API key, and nobody has to remember which fields are safe to print
    when they paste output into a ticket.
    """
    out: dict[str, str] = {}
    for field in sorted(FINGERPRINTED):
        if not hasattr(settings, field):
            # A name in the spec that the model no longer has. Reported rather
            # than raised: a stale spec entry must not stop the API booting.
            LOGGER.warning(
                "config_fingerprint: %s is classified but not a Settings field", field)
            continue
        out[field] = digest(getattr(settings, field))
    return out


def fleet_hash(settings: Any) -> str:
    """
    One hash over everything that should be identical across the fleet.

    Built from the per-field digests rather than from raw values, so the hash
    and the breakdown can never disagree about what they measured — the
    breakdown IS the input.
    """
    digests = field_digests(settings)
    payload = "\n".join("%s=%s" % (name, value) for name, value in digests.items())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def public_fingerprint(settings: Any) -> dict[str, Any]:
    """
    The unauthenticated summary: enough to detect drift, not to describe it.

    ``fields`` is a count, not a list. A node that has a setting its neighbour
    does not is already visible as a different hash; naming them publicly would
    inventory the deployment for anyone who asks.
    """
    return {
        "fleet_hash": fleet_hash(settings),
        # BESIDE the hash, never inside it. See config_spec.BUILD for why.
        # "unknown" rather than "" so a reader cannot mistake an image built
        # before this existed for a field that failed to render.
        "build": getattr(settings, "cyclone_build", "") or "unknown",
        "fields": len(FINGERPRINTED),
        "deprecated_in_use": len(deprecated_in_use(settings)),
        "legacy_supabase_keys": len(legacy_supabase_keys(settings)),
        "misplaced_secrets": len(misplaced_secrets(settings)),
    }


def detailed_fingerprint(settings: Any) -> dict[str, Any]:
    """
    The authenticated breakdown: which setting differs, without its value.

    Two of these side by side answer the question the fleet hash only raises.
    ``infrastructure`` is included and labelled, because a reader comparing two
    nodes needs to see that ``id`` differing is expected rather than wonder
    whether it was missed.
    """
    return {
        "fleet_hash": fleet_hash(settings),
        "digests": field_digests(settings),
        "buckets": {field: bucket_of(field) for field in sorted(FINGERPRINTED)},
        "secret_fields": sorted(SECRET),
        "infrastructure": {
            field: getattr(settings, field, None)
            for field in sorted(INFRASTRUCTURE)
            # Excluded from the hash by design; shown so a reader knows these
            # SHOULD differ. Not secrets -- a Supabase project URL is in the
            # frontend bundle already.
        },
        "deprecated_in_use": deprecated_in_use(settings),
        "legacy_supabase_keys": legacy_supabase_keys(settings),
        "misplaced_secrets": misplaced_secrets(settings),
    }


def deprecated_in_use(settings: Any) -> dict[str, str]:
    """
    Dead settings that still carry a value, and why each is dead.

    Reported rather than raised. ``Settings`` forbids unknown keys, so a field
    cannot be deleted until every node's ``.env`` has stopped mentioning it —
    this is what tells somebody which lines to strike before that release.

    **"In use" means somebody set it, not that it holds a value.** Two of these
    carry non-empty defaults (``db_type`` is ``"supabase"``,
    ``supabase_max_rows`` is 1000), so testing the value reported them on every
    node forever — a warning that is always on is a warning nobody reads, which
    is the exact failure this module exists to avoid. Pydantic records which
    fields an input actually supplied; that is the question being asked.
    """
    supplied = getattr(settings, "model_fields_set", None)
    found: dict[str, str] = {}
    for field in sorted(DEPRECATED):
        if supplied is not None:
            if field not in supplied:
                continue
        elif getattr(settings, field, None) in (None, "", [], 0, False):
            continue
        found[field] = DEPRECATION_REASON.get(field, "no longer read")
    return found


#: Supabase retires the legacy `anon` / `service_role` JWT keys at the end of
#: 2026. Both are in use here, in the API, the worker and the frontend bundle.
LEGACY_KEY_SUNSET = date(2026, 12, 31)

#: A legacy Supabase key is a JWT, so it starts with the base64 of `{"alg"`.
#: The replacements are opaque strings prefixed `sb_secret_` / `sb_publishable_`.
_LEGACY_KEY_PREFIX = "eyJ"

#: Which settings hold a Supabase API key, and what each is called in .env.
_SUPABASE_KEY_FIELDS = (
    ("supabase_service_role_key", "SUPABASE_SERVICE_ROLE_KEY"),
    ("supabase_anon_key", "SUPABASE_ANON_KEY"),
    ("supabase_landing_pages_service_role_key",
     "SUPABASE_LANDING_PAGES_SERVICE_ROLE_KEY"),
)


def legacy_supabase_keys(settings: Any, today: "date | None" = None) -> list[dict[str, Any]]:
    """
    Supabase keys still in the retiring JWT format, and how long is left.

    **The migration is a change of value, not of name.** ``SupabaseManager``
    reads ``SUPABASE_SERVICE_ROLE_KEY`` out of the environment itself and is a
    third-party package, so the new ``sb_secret_...`` string goes into the same
    variable. Nothing in this codebase parses an API key as a JWT — the only
    ``jwt.decode`` is in ``auth_middleware``, verifying USER tokens against
    JWKS — so the swap needs no code change at all.

    What it buys is worth more than beating the deadline: secret keys are
    created and revoked individually, where rotating ``service_role`` today
    means rotating the project JWT secret.

    Detection is on the format rather than on a flag somebody has to remember
    to set, so a node that was missed reports itself.
    """
    today = today or date.today()
    remaining = (LEGACY_KEY_SUNSET - today).days
    found: list[dict[str, Any]] = []
    for field, variable in _SUPABASE_KEY_FIELDS:
        value = getattr(settings, field, "") or ""
        if value.startswith(_LEGACY_KEY_PREFIX):
            found.append({
                "variable": variable,
                "replace_with": ("sb_publishable_..." if "ANON" in variable
                                 else "sb_secret_..."),
                "days_remaining": remaining,
                "overdue": remaining < 0,
            })
    return found


#: Settings whose value REACHES A BROWSER, and the prefix that would mean a
#: secret had been put in one.
#:
#: `supabase_anon_key` is a docker build arg compiled into the JavaScript
#: bundle; `stripe_publishable_key` is served from the unauthenticated
#: GET /api/config. Both are meant to hold a publishable credential, and both
#: are one paste away from holding the secret twin — the names differ by a word
#: and the values differ by a prefix.
#:
#: This is not hypothetical. A secret key was found sitting in
#: SUPABASE_LANDING_PAGES_ANON_KEY, where it happened to be harmless because
#: nothing read that variable. In the two below, the same mistake ships the
#: credential to every visitor.
_BROWSER_BOUND = (
    ("supabase_anon_key", "SUPABASE_ANON_KEY", ("sb_secret_",),
     "compiled into the frontend bundle by the docker build"),
    ("stripe_publishable_key", "STRIPE_PUBLISHABLE_KEY", ("sk_live_", "sk_test_"),
     "served from the unauthenticated GET /api/config"),
)


def misplaced_secrets(settings: Any) -> list[dict[str, str]]:
    """
    Secret-shaped values sitting in slots whose contents reach a browser.

    Detected by prefix, because that is the only signal available and it is a
    reliable one: vendors deliberately make the secret and publishable forms
    distinguishable at a glance, which is exactly what makes the check possible
    and the mistake easy.

    Returned rather than raised. A process that refuses to boot over this would
    take the site down to prevent a disclosure that has, in the worst case,
    already happened on the previous deploy — and an ERROR in the log plus the
    flag on the fingerprint endpoint gets it fixed within the hour without a
    second outage. It is the one judgement here that could reasonably go the
    other way.
    """
    found: list[dict[str, str]] = []
    for field, variable, prefixes, where in _BROWSER_BOUND:
        value = getattr(settings, field, "") or ""
        for prefix in prefixes:
            if value.startswith(prefix):
                found.append({
                    "variable": variable,
                    "holds": prefix + "...",
                    "exposure": where,
                })
                break
    return found


def local_defaults_in_production(settings: Any) -> list[dict[str, str]]:
    """
    Infrastructure settings nobody set, whose default points at this machine.

    **A localhost default is right in development and never right in
    production**, and the gap between those is silent. `REDIS_URL` went into
    SSM during the migration and out of `.env`; on a dev box, which has no SSM,
    `settings.redis_url` quietly fell back to ``redis://localhost:6379/0`` and
    every Redis call cost four seconds before failing. Nothing said which of the
    two situations you were in -- the fallback is indistinguishable from a
    deliberate choice.

    On a node it is worse than slow. `redis_url` at its default means the
    fleet-wide `crm:poller` lock is being taken against a Redis nobody else can
    see, so **every node believes it holds the lock** and all of them poll the
    mailbox. `host_url` at its default means CORS allows only localhost and any
    generated URL is wrong.

    Asked of `model_fields_set` rather than of the value, the same question
    `deprecated_in_use` asks: did somebody set this, or is it merely holding a
    default? A value that happens to equal the default because somebody typed it
    is a decision and is not reported.

    :return: One entry per unset field whose default names this machine.
    :rtype: list[dict[str, str]]
    """
    # Development is where these defaults are correct, and warning there would
    # make this the noise that trains people to ignore the log.
    if getattr(settings, "is_development", False):
        return []

    fields = getattr(type(settings), "model_fields", {})
    was_set = getattr(settings, "model_fields_set", set())
    found: list[dict[str, str]] = []
    for field in sorted(INFRASTRUCTURE):
        info = fields.get(field)
        if info is None or field in was_set:
            continue
        default = info.default
        if not isinstance(default, str) or not default:
            continue
        # "localhost", "127.0.0.1" and the bare id "local" are the shapes a
        # default takes when it means "this machine".
        if not ("localhost" in default or "127.0.0.1" in default
                or default == "local"):
            continue
        found.append({"variable": field.upper(), "default": default})
    return found


def log_startup_summary(settings: Any) -> None:
    """
    Say the fingerprint once at boot, and name any dead settings still set.

    The log line is how you correlate a node's behaviour with its
    configuration after the fact — the hash is in the log beside the deploy
    that produced it, so "which config was this node running on Tuesday" has an
    answer that does not depend on the file still being there.
    """
    # First, because it is the only finding here that is a disclosure rather
    # than an inconvenience.
    for bad in misplaced_secrets(settings):
        LOGGER.error(
            "%s holds a value beginning %s — that is a SECRET key in a slot that is "
            "%s. Replace it with the publishable credential and redeploy; treat the "
            "secret as disclosed and revoke it.",
            bad["variable"], bad["holds"], bad["exposure"],
        )

    # MAX_UPLOAD_MB is a Settings field that is NOT a knob, and the difference
    # is invisible from inside the process. The limit is enforced by
    # `client_max_body_size` in the frontend image's nginx.conf, which no
    # environment variable reaches: raising the setting makes the API advertise
    # a limit nginx still refuses, so the browser stops pre-checking files it
    # will then reject mid-upload with a raw error page -- the exact failure the
    # pre-check was added to remove. Lowering it is no better: uploads are
    # refused that the server would have taken, with nothing saying why.
    #
    # tests/test_upload_limit.py pins the DEFAULT against nginx.conf, which is
    # the only comparison it can make statically. This is the other half: it
    # catches somebody setting the variable at runtime, where no test can look.
    if "max_upload_mb" in getattr(settings, "model_fields_set", set()):
        LOGGER.warning(
            "MAX_UPLOAD_MB is set to %s, but the limit is enforced by nginx in the "
            "frontend image and no environment variable changes it. The API will "
            "advertise %s while nginx still refuses anything over its own "
            "client_max_body_size, so uploads between the two are rejected "
            "mid-flight. Change frontend/nginx.conf and the default together, or "
            "unset this.",
            getattr(settings, "max_upload_mb", "?"),
            getattr(settings, "max_upload_mb", "?"),
        )

    for local in local_defaults_in_production(settings):
        LOGGER.warning(
            "%s is not set on this node and fell back to %s, which names THIS "
            "machine. Nothing here is development, so that is almost certainly "
            "not what was wanted: set it in .env or in SSM under this "
            "environment's path. A Redis at localhost means every node believes "
            "it holds the fleet-wide poller lock.",
            local["variable"], local["default"],
        )

    LOGGER.info(
        "Config fingerprint | id=%s fleet_hash=%s fields=%d",
        getattr(settings, "id", "?"), fleet_hash(settings), len(FINGERPRINTED),
    )
    for field, reason in deprecated_in_use(settings).items():
        LOGGER.warning(
            "Deprecated setting in use: %s (%s). Remove the line from .env on every "
            "node; the field is deleted in a later release and startup will then fail "
            "while it is still present.", field.upper(), reason,
        )
    for stale in legacy_supabase_keys(settings):
        # WARNING rather than ERROR until it is actually overdue: the key works
        # perfectly today, and a log line nobody can act on tonight should not
        # look like a failure. It becomes an ERROR the day it stops being true.
        report = LOGGER.error if stale["overdue"] else LOGGER.warning
        report(
            "%s is a legacy Supabase JWT key, retired by Supabase on %s (%d days). "
            "Replace the VALUE with a %s key — same variable, no code change — and "
            "gain per-key revocation.",
            stale["variable"], LEGACY_KEY_SUNSET.isoformat(),
            stale["days_remaining"], stale["replace_with"],
        )

"""
app/util/config_spec.py - What each setting IS, so drift can be measured.

Two servers' ``.env`` files drifted, and it was found by luck. The fix is not a
different place to keep the file; it is being able to ask two running servers
whether they agree. That question is unanswerable without this module, because
**some settings are supposed to differ.**

``id`` is "which server answered". ``host_url`` is that server's own address. A
fingerprint taken over every setting would differ between any two machines, on
purpose, every time — which is a tool that reports drift constantly and
therefore reports it never.

So each setting lands in exactly one bucket, and only some of them are hashed:

===================  ==========================================================
``FLEET``            Must be IDENTICAL on every node in an environment. Model
                     temperatures, poll intervals, log level, feature tuning.
                     These are the drift candidates — the ones that differ
                     silently and change behaviour without failing.
``SECRET``           Credentials. Hashed by VALUE DIGEST, never by value, so
                     two nodes can be shown to hold the same key without the
                     key appearing anywhere. Also fleet-identical.
``PER_FIRM``         Belongs in a ``firms`` row once a second firm exists —
                     ``firm_name``, intake dropdowns, billing increments. In
                     the environment today, which is a single-tenant assumption
                     compiled into the deployment. Fleet-identical for now.
``INFRASTRUCTURE``   Legitimately differs per node or per environment. EXCLUDED
                     from the fingerprint; reported beside it as context.
``COMPOSE_ONLY``     Read by ``docker-compose.yml``, not by the application.
                     ``SUPABASE_ANON_KEY`` is a frontend build arg. The field
                     exists on ``Settings`` only because ``extra = "forbid"``
                     would otherwise reject the line — deleting the field would
                     stop every container booting.
``DEPRECATED``       Accepted, warned about, and excluded from the fingerprint.
                     See the note on two-phase removal below.
===================  ==========================================================

**Why deprecation is two-phase and not a delete.** ``Settings`` sets
``extra = "forbid"``, which is a drift guard worth keeping — a typo'd variable
is refused rather than silently ignored. The cost is that removing a field is a
*coordinated* change: the moment the field goes, any node whose ``.env`` still
carries the line fails to start. So a setting is first declared dead here,
which makes startup warn and tells whoever reads the log to strike the line;
the field is deleted in a later release once the fleet is clean. Item 5 of the
hardening plan (config as a build artifact) is what eventually makes that
one step instead of two.

Every field on ``Settings`` must appear in exactly one bucket, and
``tests/test_config_fingerprint.py`` fails if one does not. That is the whole
guarantee: a setting added without a decision about what it is cannot ship.
"""

# Identical across every node. The silent drift lives here: a node running
# llm_temperature 0.3 against a fleet running 0.1 produces different evidence
# and nothing anywhere says so.
FLEET = frozenset({
    "version",
    "is_development",
    "log_format",
    "log_level",
    "llm_temperature",
    "llm_top_p",
    "llm_max_tokens",
    "llm_timeout_seconds",
    "llm_profiles_file",
    "groq_base_url",
    "deepseek_base_url",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_use_tls",
    "mail_from_address",
    "mail_from_name",
    "imap_host",
    "imap_port",
    "imap_username",
    "imap_mailbox",
    "imap_spam_folder",
    "mail_timeout_seconds",
    "redis_timeout_seconds",
    "job_poll_interval_seconds",
    "job_concurrency",
    "lead_poll_interval_seconds",
    "welcome_leads_after",
})

# Credentials. Hashed by digest, never by value.
# NOTE: there is no separate `supabase_secret_key`. The Supabase key migration
# (legacy `service_role` JWT -> `sb_secret_...`) is a change of VALUE, not of
# name: db_handler's SupabaseManager reads os.environ["SUPABASE_SERVICE_ROLE_KEY"]
# directly and it is a third-party package. A second field naming the same
# credential would be a second way to say one thing, which golden rule 2 forbids
# -- and would leave two places for the fleet to disagree about which is live.
SECRET = frozenset({
    "supabase_service_role_key",
    "supabase_landing_pages_service_role_key",
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "groq_api_key",
    "deepseek_api_key",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "smtp_password",
    "imap_password",
    "telegram_bot_token",
})

# Moves to a `firms` row when a second firm is real. Until then these are the
# clearest evidence that tenancy is compiled into the deployment: all three are
# served from GET /api/config, per process, where they need to be per request.
PER_FIRM = frozenset({
    "firm_name",
    "referral_types",
    "time_increment_options",
    "default_refresh_trigger_pct",
    "stripe_publishable_key",
})

# Differs by node or environment on purpose. NOT fingerprinted.
INFRASTRUCTURE = frozenset({
    "id",
    # Where this node gets its secrets, and which environment it is in. Per-node
    # by definition -- a laptop reads .env while an EC2 box reads SSM, and that
    # difference must not read as drift.
    "cyclone_secrets_source",
    "cyclone_env",
    # Region is per-deployment. Read by `secret_source.region()` and passed to
    # boto3 explicitly -- NOT left to botocore's own environment scan, which
    # raised NoRegionError with AWS_REGION set and present in the process.
    # Optional wherever IMDS is reachable, since placement/region answers it.
    "aws_region",
    "aws_default_region",
    "host_url",
    "supabase_url",
    "supabase_landing_pages_url",
    "redis_url",
})

# Read by docker-compose, not by the app. The Settings field exists only so
# `extra = "forbid"` does not reject the line.
COMPOSE_ONLY = frozenset({
    "supabase_anon_key",
    # compose references ${API_BASE_URL} for the frontend build. It had no
    # field, so anyone who set it would have stopped the API booting.
    "api_base_url",
})

# Declared dead. Startup warns; the field is deleted once no .env carries it.
#
# NOTE ON THE TWO JWT SECRETS THAT ARE NOT HERE. `SUPABASE_JWT_SECRET` and
# `SUPABASE_ANON_JWT_SECRET` were the headline "carried for nothing" pair, and
# they turned out not to be Settings fields at all -- they existed only as lines
# in `.env.example`. Because `extra = "forbid"` refuses a key the model does not
# declare, any node whose .env carried them could never have booted; the real
# files cannot contain them, so there is nothing on a server to strike. They
# were struck from `.env.example`, which had been unusable as written.
#
# Everything below IS a declared field that nothing reads.
DEPRECATED = frozenset({
    "supabase_key",
    "supabase_password",
    "supabase_landing_pages_key",
    "supabase_landing_pages_anon_key",
    "db_type",
    "supabase_max_rows",
})

#: Why each deprecated setting is dead, printed at startup so the person
#: reading the log knows whether striking the line is safe.
DEPRECATION_REASON = {
    "supabase_key": "superseded by supabase_service_role_key",
    "supabase_password": "unused — the service role key is the credential",
    "supabase_landing_pages_key":
        "superseded by supabase_landing_pages_service_role_key",
    "supabase_landing_pages_anon_key":
        "unused — the backend never reads it; only the Cyclone anon key is a "
        "frontend build arg",
    "db_type": "unused — SupabaseManager is the only implementation",
    "supabase_max_rows": "unused — paging is per query (_PAGE in the services)",
}

#: Hashed into the fleet fingerprint. Everything that should be identical.
FINGERPRINTED = FLEET | SECRET | PER_FIRM

#: Every bucket, for the completeness check.
# Identifies the IMAGE, not the configuration. Its own bucket because it is
# neither: it is supposed to be identical across a fleet (like FLEET) yet is not
# fingerprinted (like INFRASTRUCTURE), and for a reason worth stating.
#
# Folding the build into `fleet_hash` conflates two questions -- "do these nodes
# agree about configuration?" and "are these nodes running the same code?" --
# and a single hash that disagrees then cannot say which. It also destroys a
# real technique: a node deliberately left on an older image with a fully
# populated .env is a CONTROL, and comparing a migrated node's hash against it
# is what proves a secret moved into SSM byte-for-byte. That check is only
# possible while the hash is comparable across builds.
BUILD = frozenset({
    "cyclone_build",
})

# WHICH NAMES docker-compose.yml INTERPOLATES ON THE HOST.
#
# **NOT a bucket, and it must never become one.** The buckets are disjoint and
# answer "what is this setting FOR?"; this answers a different question -- "does
# compose read it before any container exists?" -- and the answers overlap.
# `SUPABASE_ANON_KEY` is COMPOSE_ONLY, `SUPABASE_URL` is INFRASTRUCTURE because
# the API genuinely needs it too, and BOTH are interpolated.
#
# That overlap is the bug this exists to prevent, and it reached production.
# `_refuse_if_compose_reads_it` was keyed on COMPOSE_ONLY, so it never saw
# SUPABASE_URL; the name was removed from `.env` during the SSM migration, the
# next `docker compose build` substituted an EMPTY STRING into the frontend's
# VITE_SUPABASE_URL build arg, and the bundle shipped with no Supabase URL.
#
# The failure is as quiet as it gets: compose prints one "variable is not set"
# line and exits 0, the image builds, the container is healthy, nginx serves
# index.html with a 304, nothing appears in any server log -- and every browser
# gets a BLANK WHITE PAGE, because `createClient("")` throws
# `supabaseUrl is required.` at module scope before React mounts.
#
# `tests/test_config_fingerprint.py` asserts this set equals what
# docker-compose.yml actually interpolates, so it cannot drift from the file it
# describes. Adding a `${VARIABLE}` to compose without adding it here fails.
COMPOSE_INTERPOLATED = frozenset({
    "supabase_url",
    "supabase_anon_key",
    # Has an explicit `:-` default in compose, so an absent value is intended
    # rather than an accident. Still listed: it is still read on the host.
    "api_base_url",
    # Stamped into the image by the build; absent means "unknown", which
    # check-fleet reports rather than treating as agreement.
    "cyclone_build",
})

#: Of those, the ones whose absence silently breaks the build rather than
#: falling back to something sensible. These MUST be set in `.env` on whatever
#: machine runs `docker compose build`.
COMPOSE_REQUIRED = frozenset({
    "supabase_url",
    "supabase_anon_key",
})

ALL_BUCKETS = {
    "fleet": FLEET,
    "build": BUILD,
    "secret": SECRET,
    "per_firm": PER_FIRM,
    "infrastructure": INFRASTRUCTURE,
    "compose_only": COMPOSE_ONLY,
    "deprecated": DEPRECATED,
}


def bucket_of(field: str) -> str:
    """
    Which bucket a setting belongs to.

    :param field: A ``Settings`` field name.
    :return: The bucket name, or ``"unclassified"``.
    :rtype: str
    """
    for name, members in ALL_BUCKETS.items():
        if field in members:
            return name
    return "unclassified"


def unclassified(fields: "list[str] | set[str]") -> list[str]:
    """Settings that no bucket claims — a failure, not a warning."""
    return sorted(f for f in fields if bucket_of(f) == "unclassified")


def overlapping() -> list[str]:
    """Settings claimed by more than one bucket."""
    seen: dict[str, int] = {}
    for members in ALL_BUCKETS.values():
        for field in members:
            seen[field] = seen.get(field, 0) + 1
    return sorted(field for field, count in seen.items() if count > 1)

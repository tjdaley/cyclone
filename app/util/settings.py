"""
settings.py - Configuration settings for the application.

Rf. https://docs.pydantic.dev/latest/concepts/pydantic_settings/
"""
from typing import Optional
from pydantic_settings import BaseSettings

from util import secret_source

# Secrets from SSM, before anything reads configuration.
#
# This has to happen above `settings = Settings()` at the foot of the file,
# because that singleton is constructed at import and every entry point -- the
# API, the worker, the test suite -- gets configuration through it. There is no
# later hook that all three share.
#
# Off unless a node sets CYCLONE_SECRETS_SOURCE=ssm, and it never overwrites a
# value already in the environment, so this line changes nothing until somebody
# opts in. When it IS on and SSM cannot be read it raises rather than letting a
# process start half-credentialled: a worker with no Anthropic key does not
# fail, it silently does nothing.
secret_source.load()


class Settings(BaseSettings):
    # Version for pinging the API
    version: str = "2026.04.05"  # Version of the application
    # Deployment identity — which server instance answered a request. Surfaced
    # in the UI, Telegram messages, etc. e.g. "DEV", "ec2-54-84-177-70".
    id: str = "local"
    host_url: str = "http://localhost:8000"
    is_development: bool = False
    firm_name: str = "Your Law Firm - Override in .env"

  # Database type
    db_type: str = "supabase"

    # Supabase settings
    ## CYCLONE DB
    supabase_url: str = ""
    supabase_key: Optional[str] = None
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""
    ## LANDING PAGES DB
    supabase_landing_pages_url: str = ""
    supabase_landing_pages_key: Optional[str] = None
    supabase_landing_pages_service_role_key: str = ""
    supabase_landing_pages_anon_key: str = ""

    # ── Read by docker-compose, not by this app ───────────────────────────
    # These exist as fields ONLY because `extra = "forbid"` refuses any key in
    # .env that the model does not declare. docker-compose.yml passes both to
    # the frontend build (`VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL`), so
    # they legitimately live in .env — and without a field here, setting either
    # one stops the API container booting. api_base_url was exactly that trap:
    # compose already referenced it and nothing declared it.
    api_base_url: str = ""

    # ── Where secrets come from ───────────────────────────────────────────
    # Declared so `extra = "forbid"` does not refuse them in a .env; normally
    # they are set by compose or by the instance, and read from os.environ by
    # util/secret_source.py before this class is constructed.
    #
    # CYCLONE_SECRETS_SOURCE="ssm" turns on SSM loading. CYCLONE_ENV names the
    # environment for anything not on EC2 -- on EC2 the name comes from the
    # instance's own tag, so nobody types it and nobody can typo it.
    cyclone_secrets_source: str = ""
    cyclone_env: str = ""
    # Which image this process is running, baked in by app/Dockerfile at build
    # time. Reported BESIDE the fleet hash and deliberately not inside it: "do
    # these nodes agree about config" and "are these nodes running the same
    # code" are two questions, and folding them together means a disagreement
    # cannot say which one it is. Empty means an image built before this
    # existed, which check-fleet reports rather than reading as agreement.
    cyclone_build: str = ""

    # boto3 needs a region and will not reliably infer one inside a container.
    # Declared here for the same reason api_base_url is: `extra = "forbid"`
    # refuses any key in a mounted .env that the model does not know, so a
    # perfectly ordinary AWS_REGION line would otherwise stop the API booting.
    #
    # Prefer setting these in config/environments/<env>.env, which compose
    # injects as process environment and pydantic never parses as a dotenv file
    # -- extras are impossible there. Any OTHER AWS_* variable must go in that
    # file and not in .env, for exactly this reason.
    aws_region: str = ""
    aws_default_region: str = ""


    supabase_password: Optional[str] = None
    supabase_max_rows: int = 1000

    # Logging settings
    log_format: str = "%(asctime)s - %(name)-15s - %(levelname)-8s - %(message)s"
    log_level: str = "WARNING"  # Default log level for API

    # ── AI / LLM ──────────────────────────────────────────────────────────
    # Global sampling defaults. Any candidate in a profile may override them.
    llm_temperature: float = 0.1
    llm_top_p: float = 0.1
    llm_max_tokens: int = 16384
    # Per-call ceiling. Without it a hung vendor hangs the whole request and
    # failover never fires, because failover is triggered by an exception.
    llm_timeout_seconds: float = 90.0

    # Task-named profiles live in a JSON catalog, not in .env — see
    # util/llm_profiles.py. Relative paths resolve against the app package.
    llm_profiles_file: str = "config/llm_profiles.json"

    # Vendor credentials — one block per vendor, independent of the profiles
    # that reference it.
    gemini_api_key: str = ""

    openai_api_key: str = ""

    anthropic_api_key: str = ""

    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # Client intake settings
    referral_types: list[str] = ["attorney", "former client", "search", "ai", "other"]

    # Billing settings
    time_increment_options: list[float] = [0.1, 0.25, 0.5, 1.0]
    default_refresh_trigger_pct: float = 0.40

    # Stripe settings
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""  # safe to expose to frontend via GET /api/config

    # Email — SMTP (outbound) + IMAP (inbound), one shared intake mailbox
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    mail_from_address: str = ""
    mail_from_name: str = "Cyclone Intake"

    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_spam_folder: str = "Spam"
    #: Seconds to wait on a mail server before giving up, for IMAP and SMTP alike.
    #:
    #: Both ran with no timeout, which means the socket default of *never*. A
    #: mail host that accepts a connection and then stops talking would hang the
    #: CRM tick indefinitely — and the tick holds the fleet-wide poller lock, so
    #: one wedged node stops every node from polling until the lock TTL expires.
    #: A dropped handshake is the visible version of that problem; the silent
    #: hang is the worse one.
    mail_timeout_seconds: int = 30
    # Seconds to wait on Redis, connecting AND reading. Same argument as
    # mail_timeout_seconds and the same fleet-wide lock at stake: without one
    # the socket default is *never*, and a host that accepts a connection and
    # then stops talking wedges the CRM tick while it holds `crm:poller`.
    #
    # Three seconds is generous for an operation that normally takes under a
    # millisecond. It bounds a HANG, not a refusal -- a refused connection
    # already returns in about two seconds and never reaches the cap. See
    # redis_client.get_redis for the measurements.
    redis_timeout_seconds: float = 3.0

    # Telegram (agent escalations to lead responders)
    telegram_bot_token: str = ""

    # Redis / Valkey (poller locks + inbound-email idempotency)
    redis_url: str = "redis://localhost:6379/0"

    # Background job worker. Short, because somebody is watching a spinner
    # while an intake extraction runs.
    job_poll_interval_seconds: int = 3
    #: How many background jobs one worker runs at once.
    #:
    #: Statement ingestion is almost entirely waiting on an LLM, so a worker
    #: running them one at a time is idle nearly all of it. Five is a bribe with
    #: a purpose: a production split into a file per month finishes in about the
    #: time the slowest five take, where before it was the sum of all of them.
    #:
    #: Raising it trades against vendor rate limits — a 429 fails the candidate
    #: over to the next model in the chain rather than waiting, so too much
    #: concurrency quietly changes which model reads the evidence. Lower it
    #: before raising it.
    job_concurrency: int = 5

    # CRM agent poller
    lead_poll_interval_seconds: int = 60
    # Only leads created at/after this ISO timestamp are eligible for the
    # automated welcome — set to the go-live moment so the back catalog of
    # existing leads is never emailed. Empty disables welcomes entirely.
    welcome_leads_after: str = ""

    class Config:
        env_file = ".env"
        extra = "forbid"  # Pydantic will throw an error if unexpected env vars are present

    def llm_api_key(self, vendor: str) -> str:
        """
        Return the configured API key for a vendor ("" when unconfigured).

        :param vendor: Vendor id as used in an LLMCandidate.
        :type vendor: str
        :return: API key, or empty string if the vendor is unknown/unconfigured.
        :rtype: str
        """
        return {
            "gemini": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "groq": self.groq_api_key,
            "deepseek": self.deepseek_api_key,
        }.get(vendor, "")

    def getattr(self, item: str, default: Optional[str] = None):
        """Get an attribute from the settings"""
        return getattr(self, item, default)

settings = Settings()

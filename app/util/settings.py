"""
settings.py - Configuration settings for the application.

Rf. https://docs.pydantic.dev/latest/concepts/pydantic_settings/
"""
from typing import Optional
from pydantic_settings import BaseSettings


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

    # Telegram (agent escalations to lead responders)
    telegram_bot_token: str = ""

    # Redis / Valkey (poller locks + inbound-email idempotency)
    redis_url: str = "redis://localhost:6379/0"

    # Background job worker. Short, because somebody is watching a spinner
    # while an intake extraction runs.
    job_poll_interval_seconds: int = 3

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

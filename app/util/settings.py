"""
settings.py - Configuration settings for the application.

Rf. https://docs.pydantic.dev/latest/concepts/pydantic_settings/
"""
from typing import Optional, Union
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Version for pinging the API
    version: str = "2026.04.05"  # Version of the application
    host_url: str = "http://localhost:8000"
    is_development: bool = False
    firm_name: str = "Your Law Firm - Override in .env"

  # Database type
    db_type: str = "supabase"

    # Supabase settings
    supabase_url: str = ""
    supabase_key: Optional[str] = None
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_anon_key: str = ""
    supabase_anon_jwt_secret: str = ""
    supabase_max_rows: int = 1000

    # Logging settings
    log_format: str = "%(asctime)s - %(name)-15s - %(levelname)-8s - %(message)s"
    log_level: str = "WARNING"  # Default log level for API

    # AI Settings
    llm_vendor: str = "gemini"  # Options: 'gemini', 'openai', 'anthropic', 'groq'
    llm_fast_vendor: str = "gemini"  # Vendor for fast models
    llm_temperature: float = 0.1
    llm_top_p: float = 0.1

    # LLM settings
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"

    openai_api_key: str = ""
    openai_model: str = ""

    anthropic_api_key: str = ""
    anthropic_model: str = ""

    groq_api_key: str = ""
    groq_model: str = "groq/compound"
    groq_base_url: str = "https://api.groq.ai/v1/"

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-reasoner"
    deepseek_base_url: str = "https://api.deepseekr.com/v1/"

    class Config:
        env_file = ".env"
        # extra = "forbid"  # Pydantic will throw an error if unexpected env vars are present

    def getattr(self, item: str, default: Optional[str] = None):
        """Get an attribute from the settings"""
        return getattr(self, item, default)

settings = Settings()

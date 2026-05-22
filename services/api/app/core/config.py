"""Application settings loaded from environment variables."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://reachai:reachai@localhost:5432/reachai"

    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"

    calendly_client_id: str = ""
    calendly_client_secret: str = ""
    calendly_redirect_uri: str = "http://localhost:8000/v1/calendly/callback"
    calendly_api_base: str = "https://api.calendly.com"
    calendly_oauth_base: str = "https://auth.calendly.com"

    admin_api_key: str = "change-me-in-production"
    session_secret_key: str = "change-me-in-production"

    allowed_origins: str = "*"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

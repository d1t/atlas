from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    app_secret_key: str = "dev-secret-change-me"

    database_url: str = "sqlite+aiosqlite:///./atlas.db"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "mock"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-sonnet-latest"

    # Third-party integrations (all optional; empty string => offline fallback)
    tavily_api_key: str = ""
    tavily_max_results: int = 10
    # DuckDuckGo HTML scraping — no key, no signup. Disable if the
    # deployment environment blocks outbound DDG traffic.
    ddg_search_enabled: bool = True
    ddg_max_results: int = 10
    site_crawler_enabled: bool = True
    site_crawler_max_per_discovery: int = 8

    # Hunter.io email-finding API (https://hunter.io/api-documentation/v2).
    # Leave empty to use deterministic mock contacts in dev/test.
    hunter_api_key: str = ""

    # Gmail (SMTP send + IMAP read) via an App Password. All optional; when the
    # address / app password are empty the integration runs in OFFLINE mode:
    # outbound emails are recorded but not actually transmitted, and reply-sync
    # is a no-op. This keeps the whole flow testable without credentials.
    gmail_address: str = ""
    gmail_app_password: str = ""
    gmail_from_name: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    # How many days back to scan the inbox when syncing replies.
    imap_lookback_days: int = 14

    cors_origins: str = "http://localhost:3000"

    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def gmail_configured(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
